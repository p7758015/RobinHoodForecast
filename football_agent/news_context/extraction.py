"""Rule-based extraction from Brave search snippets (fail-soft)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from football_agent.news_context.models import (
    CoachContextBlock,
    CoachPrioritySignal,
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
_TACTICAL_RE = re.compile(r"\bformation\b|\b3-5-2\b|\b4-3-3\b|\bhigh press\b|\btactical\b", re.I)
_INJURY_RE = re.compile(r"\binjur(y|ies|ed)\b|\bdoubtful\b|\bfitness\b|\bknock\b", re.I)
_SUSPENSION_RE = re.compile(r"\bsuspend(ed|sion)\b|\bred card\b|\bmiss(es)?\s+through\b", re.I)
_LINEUP_RE = re.compile(r"\blineup\b|\bstarting xi\b|\bprobable team\b|\bpredicted\b", re.I)
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
    r"(?:coach|manager|head coach)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    re.I,
)
_TENURE_DAYS_RE = re.compile(r"(\d+)\s+days?\s+(?:in charge|as coach|since appointment)", re.I)


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
        out.append(
            NewsSourceRef(
                title=h.title,
                url=h.url,
                source_name=h.source_name,
                published_at=h.published_at,
                snippet=h.description,
                reliability="MEDIUM" if h.published_at else "LOW",
                topic_tags=list(h.topic_tags),
            ),
        )
    return out


def extract_coach_block(
    *,
    hits: List[BraveSearchHit],
    home_team: str,
    away_team: str,
    home_coach_hint: Optional[str] = None,
    away_coach_hint: Optional[str] = None,
) -> CoachContextBlock:
    blob = _text_blob(hits)
    coach_hits = [h for h in hits if "coach" in h.topic_tags or "h2h" in h.topic_tags]
    coach_blob = _text_blob(coach_hits) if coach_hits else blob

    home_name = home_coach_hint
    away_name = away_coach_hint
    if not home_name:
        if home_team.lower() in coach_blob.lower():
            m = _COACH_NAME_RE.search(coach_blob)
            if m:
                home_name = m.group(1).strip()
    if not away_name:
        m = _COACH_NAME_RE.search(coach_blob[coach_blob.lower().find(away_team.lower()) :] if away_team else coach_blob)
        if m:
            away_name = m.group(1).strip()

    h2h_total, h2h_summary = _coach_h2h_from_text(coach_blob)
    tenure_m = _TENURE_DAYS_RE.search(coach_blob)

    missing: List[str] = []
    if not home_name:
        missing.append("home_coach_name")
    if not away_name:
        missing.append("away_coach_name")
    if h2h_total is None:
        missing.append("coach_h2h_total_matches")

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

    return CoachContextBlock(
        home_coach_name=home_name,
        away_coach_name=away_name,
        home_coach_status=_detect_status(coach_blob),
        away_coach_status=_detect_status(coach_blob),
        home_coach_tenure_days=int(tenure_m.group(1)) if tenure_m else None,
        away_coach_tenure_days=_away_tenure_days(coach_blob, away_team) if away_team else None,
        home_coach_recent_quotes=_extract_quotes(coach_blob, limit=2),
        away_coach_recent_quotes=_extract_quotes(coach_blob, limit=2)[-1:] if away_name else [],
        home_coach_rotation_signal=_first_match_signal(coach_blob, _ROTATION_RE),
        away_coach_rotation_signal=_first_match_signal(coach_blob, _ROTATION_RE),
        home_coach_morale_signal=_first_match_signal(coach_blob, _MORALE_RE),
        away_coach_morale_signal=_first_match_signal(coach_blob, _MORALE_RE),
        home_coach_tactical_signal=_first_match_signal(coach_blob, _TACTICAL_RE),
        away_coach_tactical_signal=_first_match_signal(coach_blob, _TACTICAL_RE),
        coach_priority_signal=_priority_signal(coach_blob),
        coach_h2h_total_matches=h2h_total,
        coach_h2h_recent_summary=h2h_summary,
        coach_h2h_confidence="MEDIUM" if h2h_total else "LOW" if coach_blob.strip() else "UNKNOWN",
        coach_news_confidence=confidence,
        coach_context_sources=hits_to_sources(coach_hits or hits[:5]),
        coach_context_generated_at_utc=datetime.now(timezone.utc),
        missing_fields=missing,
        warnings=[] if hits else ["coach_news_no_results"],
    )


def extract_general_news_block(
    *,
    hits: List[BraveSearchHit],
    home_team: str,
    away_team: str,
) -> GeneralNewsBlock:
    blob = _text_blob(hits)

    def collect(pat: re.Pattern[str], limit: int = 3) -> List[str]:
        found: List[str] = []
        for m in pat.finditer(blob):
            snippet = blob[max(0, m.start() - 30) : min(len(blob), m.end() + 70)].strip()
            if snippet and snippet not in found:
                found.append(snippet)
            if len(found) >= limit:
                break
        return found

    confidence = 0.0
    if hits:
        confidence = 0.3
    if collect(_INJURY_RE):
        confidence += 0.15
    if collect(_LINEUP_RE):
        confidence += 0.15
    if len(hits) >= 4:
        confidence += 0.1
    confidence = min(1.0, confidence)

    return GeneralNewsBlock(
        injuries_signals=collect(_INJURY_RE),
        suspension_signals=collect(_SUSPENSION_RE),
        predicted_lineup_signals=collect(_LINEUP_RE),
        locker_room_signals=collect(_LOCKER_RE),
        motivation_signals=collect(_MORALE_RE),
        schedule_pressure_signals=collect(_ROTATION_RE),
        derby_or_rivalry_signal=_first_match_signal(blob, _DERBY_RE),
        weather_or_travel_signal=_first_match_signal(blob, _WEATHER_RE),
        general_news_confidence=confidence,
        general_news_sources=hits_to_sources(hits[:8]),
        missing_fields=[] if hits else ["general_news_no_results"],
        warnings=[] if hits else ["general_news_no_results"],
    )
