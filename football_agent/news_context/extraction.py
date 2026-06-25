"""Rule-based extraction from Brave search snippets (fail-soft)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from football_agent.news_context.coach_normalize import normalize_coach_name
from football_agent.news_context.source_reliability import source_reliability
from football_agent.news_context.team_scope import (
    TeamScope,
    build_team_scope,
    classify_ownership,
    extract_coach_name_scoped,
    ownership_allows_side,
    split_signals_by_side,
)
from football_agent.news_context.coach_sync import sync_coach_context_block
from football_agent.news_context.models import (
    CoachContextBlock,
    CoachNewsContextBlock,
    CoachPrioritySignal,
    CoachStatContextBlock,
    CoachStatus,
    GeneralNewsBlock,
    NewsSourceRef,
)
from football_agent.services.brave_search_client import BraveSearchHit

_COACH_STATUS_PATTERNS: list[tuple[CoachStatus, re.Pattern[str]]] = [
    ("interim", re.compile(r"\binterim\b", re.I)),
    ("caretaker", re.compile(r"\bcaretaker\b", re.I)),
    ("suspended", re.compile(r"\bsuspended\b|\bban(ned)?\b", re.I)),
    ("absent", re.compile(r"\babsent\b|\bwill not be\b|\bmiss(es)?\b.*\bpress\b", re.I)),
]

_ROTATION_RE = re.compile(r"\brotation\b|\brotate\b|\brest(ed|ing)?\b|\bchanged?\s+lineup\b", re.I)
_MORALE_RE = re.compile(
    r"\bconfident\b|\bmorale\b|\bpressure\b|\bmust[- ]win\b|\bcrisis\b|\bunder fire\b",
    re.I,
)
_MOTIVATION_PT_RE = re.compile(
    r"\blanterna\b|\bmira\s+g-?6\b|\bembalad[oa]\b|\bzona de rebaixamento\b|\bz4\b|"
    r"\bperman[eê]ncia\b|\bbrigando\b.*\bacesso\b|\btr[eê]s pontos\b",
    re.I,
)
_TACTICAL_RE = re.compile(r"\bformation\b|\b3-5-2\b|\b4-3-3\b|\bhigh press\b|\btactical\b", re.I)
_INJURY_RE = re.compile(
    r"\binjur(y|ies|ed)\b|\bdoubtful\b|\bfitness\b|\bknock\b|"
    r"\bdesfalque\b|\blesionad[oa]\b|\bcontundid[oa]\b|\bfora\b.*\bjogo\b",
    re.I,
)
_SUSPENSION_RE = re.compile(
    r"\bsuspend(ed|sion)\b|\bred card\b|\bmiss(es)?\s+through\b|\bsuspens|cart[aã]o amarelo",
    re.I,
)
_LINEUP_RE = re.compile(
    r"\blineup\b|\bstarting xi\b|\bprobable team\b|\bpredicted\b|"
    r"\bescala[cç][aã]o prov[aá]vel\b|\bprov[aá]vel escala[cç][aã]o\b",
    re.I,
)
_LOCKER_RE = re.compile(r"\blocker room\b|\bdressing room\b|\bunrest\b|\bconflict\b", re.I)
_DERBY_RE = re.compile(r"\bderby\b|\brivalry\b|\bgrudge\b", re.I)
_WEATHER_RE = re.compile(r"\bweather\b|\brain\b|\bheat\b|\btravel\b|\bjet lag\b", re.I)
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{20,200})[\"”]")
_COACH_H2H_RE = re.compile(
    r"(?:met|faced|played against)\s+(\d+)\s+times?|"
    r"head[- ]to[- ]head.*?(\d+)\s+meetings?",
    re.I,
)
_COACH_NAME_RE = re.compile(
    r"(?:coach|manager|head coach|t[eé]cnico)\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+){0,2})",
    re.I,
)
_TENURE_DAYS_RE = re.compile(r"(\d+)\s+days?\s+(?:in charge|as coach|since appointment)", re.I)


def _team_tokens(team: str) -> set[str]:
    folded = fold_text(team)
    tokens = {folded}
    for part in folded.replace("-", " ").split():
        if len(part) >= 4:
            tokens.add(part)
    return tokens


def _text_mentions_team(text: str, team: str) -> bool:
    folded = fold_text(text)
    return any(tok and tok in folded for tok in _team_tokens(team))


def _sentences(blob: str) -> List[str]:
    return [s.strip() for s in re.split(r"[.!?\n]+", blob) if s.strip()]


def _extract_coach_name_for_team(
    blob: str,
    team: str,
    *,
    exclude_team: Optional[str] = None,
) -> Optional[str]:
    """Team-scoped coach name extraction (PT/EN)."""
    if not team:
        return None
    for sentence in _sentences(blob):
        if not _text_mentions_team(sentence, team):
            continue
        if exclude_team and _text_mentions_team(sentence, exclude_team):
            if not _text_mentions_team(sentence, team):
                continue
        m = _COACH_NAME_RE.search(sentence)
        if m:
            name = normalize_coach_name(m.group(1).strip())
            if name:
                return name
    if _text_mentions_team(blob, team):
        m = _COACH_NAME_RE.search(blob)
        if m:
            return normalize_coach_name(m.group(1).strip())
    return None


def _away_tenure_days(blob: str, away_team: str) -> Optional[int]:
    if not away_team:
        return None
    idx = blob.lower().find(away_team.lower())
    segment = blob[idx:] if idx >= 0 else blob
    m = _TENURE_DAYS_RE.search(segment)
    if m:
        return int(m.group(1))
    return None


def _text_blob(hits: Iterable[BraveSearchHit]) -> str:
    parts: List[str] = []
    for h in hits:
        parts.append(h.title or "")
        parts.append(h.description or "")
    return "\n".join(parts)


def _detect_status(text: str) -> CoachStatus:
    for status, pat in _COACH_STATUS_PATTERNS:
        if pat.search(text):
            return status
    return "active" if text.strip() else "unknown"


def _first_match_signal(text: str, pattern: re.Pattern[str]) -> Optional[str]:
    m = pattern.search(text)
    if not m:
        return None
    start = max(0, m.start() - 40)
    end = min(len(text), m.end() + 80)
    return text[start:end].strip()


def _extract_quotes(text: str, limit: int = 3) -> List[str]:
    quotes = []
    for m in _QUOTE_RE.finditer(text):
        q = m.group(1).strip()
        if len(q) >= 20:
            quotes.append(q)
        if len(quotes) >= limit:
            break
    return quotes


def _coach_h2h_from_text(text: str) -> Tuple[Optional[int], Optional[str]]:
    m = _COACH_H2H_RE.search(text)
    if not m:
        return None, None
    for g in m.groups():
        if g and g.isdigit():
            n = int(g)
            snippet = text[max(0, m.start() - 20) : min(len(text), m.end() + 60)].strip()
            return n, snippet
    return None, None


def _priority_signal(text: str) -> CoachPrioritySignal:
    low = text.lower()
    if "must win" in low or "must-win" in low:
        return "must_win_language"
    if "rotation" in low or "rotate" in low:
        return "rotation_expected"
    if "cup" in low and ("priority" in low or "focus" in low):
        return "cup_priority"
    if "league" in low and ("priority" in low or "focus" in low):
        return "league_priority"
    if "pressure" in low or "morale" in low or "crisis" in low:
        return "morale_pressure"
    return "none"


def hits_to_sources(hits: Iterable[BraveSearchHit]) -> List[NewsSourceRef]:
    out: List[NewsSourceRef] = []
    for h in hits:
        level, _weight = source_reliability(h.url, h.source_name)
        out.append(
            NewsSourceRef(
                title=h.title,
                url=h.url,
                source_name=h.source_name,
                published_at=h.published_at,
                snippet=h.description,
                reliability=level,
                topic_tags=list(h.topic_tags),
            ),
        )
    return out


def _best_coach_for_side(sentences: List[str], scope: TeamScope, side: str) -> tuple[Optional[str], float]:
    best_name: Optional[str] = None
    best_conf = 0.0
    for sentence in sentences:
        name, conf = extract_coach_name_scoped(sentence, scope, side=side)
        if name and conf > best_conf:
            best_name, best_conf = name, conf
    return best_name, best_conf


def extract_coach_block(
    *,
    hits: List[BraveSearchHit],
    home_team: str,
    away_team: str,
    home_coach_hint: Optional[str] = None,
    away_coach_hint: Optional[str] = None,
    competition_country: Optional[str] = None,
) -> CoachContextBlock:
    scope = build_team_scope(home_team, away_team, competition_country=competition_country)
    sentences = _sentences(_text_blob(hits))
    coach_hits = [h for h in hits if "coach" in (h.topic_tags or []) or "h2h" in (h.topic_tags or [])]
    coach_blob = _text_blob(coach_hits) if coach_hits else _text_blob(hits)

    home_name = normalize_coach_name(home_coach_hint)
    away_name = normalize_coach_name(away_coach_hint)
    home_conf = 0.85 if home_name else 0.0
    away_conf = 0.85 if away_name else 0.0

    if not home_name:
        home_name, home_conf = _best_coach_for_side(sentences, scope, "home")
    if not away_name:
        away_name, away_conf = _best_coach_for_side(sentences, scope, "away")
    if home_name and away_name == home_name:
        away_name, away_conf = None, 0.0

    h2h_total, h2h_summary = _coach_h2h_from_text(coach_blob)
    tenure_m = _TENURE_DAYS_RE.search(coach_blob)

    missing: List[str] = []
    if not home_name:
        missing.append("home_coach_name")
    if not away_name:
        missing.append("away_coach_name")

    confidence = 0.0
    if hits:
        confidence = 0.25
    if home_name or away_name:
        confidence += 0.2
    if _extract_quotes(coach_blob):
        confidence += 0.15
    if h2h_total is not None:
        confidence += 0.15
    if len(hits) >= 3:
        confidence += 0.15
    confidence = min(1.0, confidence)

    home_status = "unknown"
    away_status = "unknown"
    for sentence in sentences:
        own = classify_ownership(sentence, scope)
        if ownership_allows_side(own, "home"):
            home_status = _detect_status(sentence)
        if ownership_allows_side(own, "away"):
            away_status = _detect_status(sentence)

    news = CoachNewsContextBlock(
        home_coach_name=home_name,
        away_coach_name=away_name,
        home_coach_confidence=home_conf,
        away_coach_confidence=away_conf,
        home_coach_status=home_status,
        away_coach_status=away_status,
        home_coach_recent_quotes=_extract_quotes(coach_blob, limit=2),
        away_coach_recent_quotes=_extract_quotes(coach_blob, limit=2)[-1:] if away_name else [],
        home_coach_rotation_signal=_first_match_signal(coach_blob, _ROTATION_RE),
        away_coach_rotation_signal=_first_match_signal(coach_blob, _ROTATION_RE),
        home_coach_morale_signal=_first_match_signal(coach_blob, _MORALE_RE),
        away_coach_morale_signal=_first_match_signal(coach_blob, _MORALE_RE),
        home_coach_tactical_signal=_first_match_signal(coach_blob, _TACTICAL_RE),
        away_coach_tactical_signal=_first_match_signal(coach_blob, _TACTICAL_RE),
        coach_priority_signal=_priority_signal(coach_blob),
        coach_news_confidence=confidence,
        coach_context_sources=hits_to_sources(coach_hits or hits[:5]),
        coach_context_generated_at_utc=datetime.now(timezone.utc),
        missing_fields=missing,
        warnings=[] if hits else ["coach_news_no_results"],
    )

    stat = CoachStatContextBlock(
        home_coach_tenure_days=int(tenure_m.group(1)) if tenure_m else None,
        away_coach_tenure_days=_away_tenure_days(coach_blob, away_team) if away_team else None,
        coach_h2h_total_matches=h2h_total,
        coach_h2h_recent_summary=h2h_summary,
        coach_h2h_confidence="MEDIUM" if h2h_total else "LOW" if coach_blob.strip() else "UNKNOWN",
        stat_source="brave_news_heuristic" if h2h_total or tenure_m else "none",
        missing_fields=["coach_h2h_total_matches"] if h2h_total is None else [],
    )

    block = CoachContextBlock(news=news, stat=stat)
    return sync_coach_context_block(block)


def extract_general_news_block(
    *,
    hits: List[BraveSearchHit],
    home_team: str,
    away_team: str,
    competition_country: Optional[str] = None,
    home_coach_hint: Optional[str] = None,
    away_coach_hint: Optional[str] = None,
) -> GeneralNewsBlock:
    scope = build_team_scope(home_team, away_team, competition_country=competition_country)
    coach_kw = {"home_coach": home_coach_hint, "away_coach": away_coach_hint}
    blob = _text_blob(hits)

    def collect(pat: re.Pattern[str], limit: int = 6) -> List[str]:
        found: List[str] = []
        for m in pat.finditer(blob):
            snippet = blob[max(0, m.start() - 30) : min(len(blob), m.end() + 70)].strip()
            if snippet and snippet not in found:
                found.append(snippet)
            if len(found) >= limit:
                break
        return found

    injury_raw = collect(_INJURY_RE)
    susp_raw = collect(_SUSPENSION_RE)
    motivation_raw = collect(_MORALE_RE, limit=2) + collect(_MOTIVATION_PT_RE, limit=4)

    home_inj, away_inj, un_inj = split_signals_by_side(injury_raw, scope, **coach_kw)
    home_susp, away_susp, un_susp = split_signals_by_side(susp_raw, scope, **coach_kw)
    home_mot, away_mot, un_mot = split_signals_by_side(motivation_raw, scope, **coach_kw)

    confidence = 0.0
    if hits:
        confidence = 0.3
    if home_inj or away_inj:
        confidence += 0.15
    if collect(_LINEUP_RE):
        confidence += 0.15
    if home_mot or away_mot:
        confidence += 0.1
    if len(hits) >= 4:
        confidence += 0.1
    confidence = min(1.0, confidence)

    unassigned = list(dict.fromkeys(un_inj + un_susp + un_mot))[:5]

    return GeneralNewsBlock(
        injuries_signals=home_inj + away_inj,
        suspension_signals=home_susp + away_susp,
        home_injuries_signals=home_inj,
        away_injuries_signals=away_inj,
        home_suspension_signals=home_susp,
        away_suspension_signals=away_susp,
        predicted_lineup_signals=collect(_LINEUP_RE),
        locker_room_signals=collect(_LOCKER_RE),
        motivation_signals=home_mot + away_mot,
        home_motivation_signals=home_mot,
        away_motivation_signals=away_mot,
        unassigned_signals=unassigned,
        schedule_pressure_signals=collect(_ROTATION_RE),
        derby_or_rivalry_signal=_first_match_signal(blob, _DERBY_RE),
        weather_or_travel_signal=_first_match_signal(blob, _WEATHER_RE),
        general_news_confidence=confidence,
        general_news_sources=hits_to_sources(hits[:8]),
        missing_fields=[] if hits else ["general_news_no_results"],
        warnings=(["unassigned_signals_present"] if unassigned else []) if hits else ["general_news_no_results"],
    )
