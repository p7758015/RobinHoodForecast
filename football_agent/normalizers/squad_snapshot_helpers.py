"""Flashscore squad_raw + Brave hints → SquadContextV2 (honest partial enrichment)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from football_agent.domain.enums_v2 import AvailabilityStatus, PlayerImportance
from football_agent.domain.models_v2 import PlayerAvailabilityV2, PlayerRefV2, SquadContextV2, TeamRefV2
from football_agent.flashscore.models import FlashscoreSquadRaw
from football_agent.news_context.coach_normalize import fold_text
from football_agent.news_context.factor_mapping import extract_player_hint_from_signal
from football_agent.news_context.models import GeneralNewsBlock, MatchNewsContext

_KEY_ROLE_PATTERNS: List[Tuple[re.Pattern[str], PlayerImportance]] = [
    (re.compile(r"\b(gk|goalkeeper|keeper|goleir[oa])\b", re.I), PlayerImportance.CRITICAL),
    (re.compile(r"\b(cb|centre.?back|center.?back|defender|defence)\b", re.I), PlayerImportance.HIGH),
    (re.compile(r"\b(dm|defensive.?mid|midfield.?anchor)\b", re.I), PlayerImportance.HIGH),
    (re.compile(r"\b(striker|forward|cf|centre.?forward|center.?forward)\b", re.I), PlayerImportance.HIGH),
    (re.compile(r"\b(playmaker|attacking.?mid|am|no\.?\s*10)\b", re.I), PlayerImportance.HIGH),
    (re.compile(r"\b(captain|star|key)\b", re.I), PlayerImportance.HIGH),
]

_STATUS_ALIASES: Dict[str, AvailabilityStatus] = {
    "injured": AvailabilityStatus.INJURED,
    "injury": AvailabilityStatus.INJURED,
    "out": AvailabilityStatus.INJURED,
    "doubtful": AvailabilityStatus.DOUBTFUL,
    "doubt": AvailabilityStatus.DOUBTFUL,
    "questionable": AvailabilityStatus.DOUBTFUL,
    "suspended": AvailabilityStatus.SUSPENDED,
    "suspension": AvailabilityStatus.SUSPENDED,
    "ban": AvailabilityStatus.SUSPENDED,
    "suspenso": AvailabilityStatus.SUSPENDED,
    "suspensão": AvailabilityStatus.SUSPENDED,
    "available": AvailabilityStatus.AVAILABLE,
}


def clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _importance_from_text(*parts: Optional[str]) -> PlayerImportance:
    blob = " ".join(p for p in parts if p).lower()
    if not blob:
        return PlayerImportance.MEDIUM
    for pattern, importance in _KEY_ROLE_PATTERNS:
        if pattern.search(blob):
            return importance
    return PlayerImportance.MEDIUM


def _parse_status_token(token: Any) -> AvailabilityStatus:
    text = str(token or "").strip().lower()
    if not text:
        return AvailabilityStatus.UNKNOWN
    for key, status in _STATUS_ALIASES.items():
        if key in text:
            return status
    return AvailabilityStatus.UNKNOWN


def _parse_player_entry(entry: Any) -> Tuple[str, AvailabilityStatus, PlayerImportance, Optional[str], float]:
    """
    Parse one missing/status entry (string or dict).

    Returns: name, status, importance, reason, entry_confidence
    """
    if isinstance(entry, dict):
        name = str(entry.get("name") or entry.get("player") or entry.get("player_name") or "").strip()
        status_raw = entry.get("status") or entry.get("availability") or entry.get("type")
        reason = entry.get("reason") or entry.get("detail") or entry.get("note")
        position = entry.get("position") or entry.get("role")
        importance_raw = str(entry.get("importance") or "").upper()
        if importance_raw in PlayerImportance.__members__:
            importance = PlayerImportance[importance_raw]
        else:
            importance = _importance_from_text(name, position, reason)
        status = _parse_status_token(status_raw or reason)
        confirmed = bool(entry.get("confirmed") or entry.get("official"))
        conf = 0.7 if confirmed else 0.45
        if not name and reason:
            name = str(reason)[:80]
        return name or "unknown", status, importance, str(reason) if reason else None, conf

    text = str(entry or "").strip()
    if not text:
        return "unknown", AvailabilityStatus.UNKNOWN, PlayerImportance.MEDIUM, None, 0.35

    status = _parse_status_token(text)
    importance = _importance_from_text(text)
    return text, status, importance, text, 0.4


def _player_refs_from_names(names: List[str], *, confirmed: bool) -> List[PlayerRefV2]:
    out: List[PlayerRefV2] = []
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name or name.lower() in ("unknown", "tbd", "?"):
            continue
        out.append(PlayerRefV2(name=name))
    return out


def _availability_from_entry(
    name: str,
    status: AvailabilityStatus,
    importance: PlayerImportance,
    reason: Optional[str],
    confidence: float,
) -> PlayerAvailabilityV2:
    return PlayerAvailabilityV2(
        player=PlayerRefV2(name=name),
        status=status,
        importance=importance,
        reason=reason,
        confidence=clip01(confidence),
    )


def _parse_side_status_map(
    player_status_raw: Dict[str, Dict[str, str]],
    side: str,
) -> Tuple[List[PlayerAvailabilityV2], List[PlayerAvailabilityV2], List[PlayerAvailabilityV2]]:
    """Parse player_status_raw side map into missing / suspended / doubtful lists."""
    missing: List[PlayerAvailabilityV2] = []
    suspended: List[PlayerAvailabilityV2] = []
    doubtful: List[PlayerAvailabilityV2] = []

    side_map = player_status_raw.get(side) or {}
    if not isinstance(side_map, dict):
        return missing, suspended, doubtful

    for player_name, status_text in side_map.items():
        name = str(player_name or "").strip()
        if not name:
            continue
        status = _parse_status_token(status_text)
        importance = _importance_from_text(name, status_text)
        entry = _availability_from_entry(name, status, importance, str(status_text), 0.55)
        if status == AvailabilityStatus.SUSPENDED:
            suspended.append(entry)
        elif status == AvailabilityStatus.DOUBTFUL:
            doubtful.append(entry)
        elif status in (AvailabilityStatus.INJURED, AvailabilityStatus.UNKNOWN):
            missing.append(entry)

    return missing, suspended, doubtful


def _merge_availability_lists(
    primary: List[PlayerAvailabilityV2],
    extra: List[PlayerAvailabilityV2],
) -> List[PlayerAvailabilityV2]:
    seen = {a.player.name.lower() for a in primary}
    out = list(primary)
    for item in extra:
        key = item.player.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _count_key_absences(*lists: List[PlayerAvailabilityV2]) -> int:
    return sum(
        1
        for group in lists
        for item in group
        if item.importance in (PlayerImportance.HIGH, PlayerImportance.CRITICAL)
    )


def _key_absence_impact_score(
    missing: List[PlayerAvailabilityV2],
    suspended: List[PlayerAvailabilityV2],
    doubtful: List[PlayerAvailabilityV2],
    *,
    signal_confidence: float,
) -> float:
    """Transparent heuristic: more key-role absences → higher impact, scaled by signal quality."""
    impact = 0.0
    for item in list(missing) + list(suspended):
        if item.importance == PlayerImportance.CRITICAL:
            impact += 0.22
        elif item.importance == PlayerImportance.HIGH:
            impact += 0.14
        elif item.importance == PlayerImportance.MEDIUM:
            impact += 0.06
    for item in doubtful:
        if item.importance in (PlayerImportance.CRITICAL, PlayerImportance.HIGH):
            impact += 0.08
        else:
            impact += 0.03
    return clip01(impact * (0.55 + 0.45 * signal_confidence))


def _availability_score(
    xi_conf: float,
    line_stab: float,
    missing_count: int,
    key_missing: int,
    doubtful_count: int,
    key_absence_impact: float,
) -> float:
    base = 0.45 * xi_conf + 0.35 * line_stab + 0.20 * (1.0 - key_absence_impact)
    if missing_count > 0:
        base -= min(0.2, 0.04 * missing_count)
    if key_missing > 0:
        base -= min(0.15, 0.06 * key_missing)
    if doubtful_count > 0:
        base -= min(0.1, 0.03 * doubtful_count)
    return clip01(base)


def _news_hints_for_side(
    news: Optional[MatchNewsContext],
    *,
    side: str,
    home_team: str,
    away_team: str,
) -> Tuple[List[PlayerAvailabilityV2], List[PlayerAvailabilityV2], List[str], List[PlayerAvailabilityV2]]:
    """Brave injury/suspension/lineup hints — counts and role mentions only, no fake names."""
    if news is None or news.general_news is None:
        return [], [], [], []

    gn: GeneralNewsBlock = news.general_news
    team_name = home_team if side == "home" else away_team

    role_notes: List[str] = []
    doubtful: List[PlayerAvailabilityV2] = []
    missing: List[PlayerAvailabilityV2] = []
    suspended_from_news: List[PlayerAvailabilityV2] = []

    injury_signals = (
        list(gn.home_injuries_signals or []) if side == "home" else list(gn.away_injuries_signals or [])
    )
    suspension_signals = (
        list(gn.home_suspension_signals or []) if side == "home" else list(gn.away_suspension_signals or [])
    )

    def _text_mentions_team(text: str, team: str) -> bool:
        folded = fold_text(text)
        team_fold = fold_text(team)
        tokens = {team_fold}
        for part in team_fold.replace("-", " ").split():
            if len(part) >= 4:
                tokens.add(part)
        return any(tok and tok in folded for tok in tokens)

    def _signal_belongs_to_side(text: str, team: str, home: str, away: str) -> bool:
        if _text_mentions_team(text, team):
            return True
        if not _text_mentions_team(text, home) and not _text_mentions_team(text, away):
            return True
        return False

    if not injury_signals and not suspension_signals:
        injury_signals = [
            s for s in (gn.injuries_signals or [])
            if _signal_belongs_to_side(str(s), team_name, home_team, away_team)
        ]
        suspension_signals = [
            s for s in (gn.suspension_signals or [])
            if _signal_belongs_to_side(str(s), team_name, home_team, away_team)
        ]

    for signal in injury_signals + suspension_signals:
        text = str(signal).strip()
        if not text:
            continue
        role_notes.append(text)
        status = (
            AvailabilityStatus.SUSPENDED
            if any(
                w in fold_text(text)
                for w in ("suspend", "ban", "red card", "suspenso", "suspens", "cartao amarelo")
            )
            else AvailabilityStatus.INJURED
        )
        player_name, role_hint = extract_player_hint_from_signal(text)
        if player_name and player_name.lower().split()[0] in {
            "arqueiro", "amarelo", "estão", "estao", "recebeu", "cartão", "cartao",
        }:
            player_name = None
        if any(w in fold_text(text) for w in ("suspenso", "suspens", "suspended", "suspension")):
            status = AvailabilityStatus.SUSPENDED
        if status == AvailabilityStatus.SUSPENDED and not player_name:
            continue
        importance = _importance_from_text(text, role_hint or "")
        if role_hint == "goalkeeper":
            importance = PlayerImportance.CRITICAL
        display_name = player_name or f"news_hint:{text[:48]}"
        entry = _availability_from_entry(
            display_name,
            status,
            importance,
            text,
            clip01((news.confidence or 0.35) * 0.85),
        )
        if status == AvailabilityStatus.SUSPENDED:
            suspended_from_news.append(entry)
        else:
            missing.append(entry)

    for signal in list(gn.predicted_lineup_signals or []):
        text = str(signal).strip()
        if not text or not _signal_belongs_to_side(text, team_name, home_team, away_team):
            continue
        if any(w in text.lower() for w in ("doubt", "uncertain", "fitness", "late")):
            role_notes.append(text)
            doubtful.append(
                _availability_from_entry(
                    f"news_hint:{text[:48]}",
                    AvailabilityStatus.DOUBTFUL,
                    _importance_from_text(text),
                    text,
                    clip01((news.confidence or 0.35) * 0.75),
                ),
            )

    return missing, doubtful, role_notes, suspended_from_news


def squad_context_from_raw(
    squad: Optional[FlashscoreSquadRaw],
    team_ref: TeamRefV2,
    *,
    side: str,
    news_context: Optional[MatchNewsContext] = None,
    home_team: str = "",
    away_team: str = "",
) -> SquadContextV2:
    if squad is None:
        news_missing, news_doubtful, _, news_suspended = _news_hints_for_side(
            news_context,
            side=side,
            home_team=home_team,
            away_team=away_team,
        )
        if not news_missing and not news_doubtful and not news_suspended:
            return SquadContextV2(team=team_ref, starting_xi_confidence=0.2, line_stability_score=0.35)

        missing_entries = list(news_missing)
        doubtful = list(news_doubtful)
        suspended = list(news_suspended)
        missing_count = len(missing_entries)
        doubtful_count = len(doubtful)
        suspended_count = len(suspended)
        key_missing = _count_key_absences(missing_entries, suspended, doubtful)
        signal_confidence = clip01((news_context.confidence or 0.35) * 0.9) if news_context else 0.35
        key_impact = _key_absence_impact_score(
            missing_entries, suspended, doubtful, signal_confidence=signal_confidence,
        )
        xi_conf = max(0.12, 0.2 - min(0.12, 0.04 * key_missing))
        line_stab = max(0.15, 0.35 - min(0.12, 0.04 * key_missing))
        avail = _availability_score(
            xi_conf, line_stab, missing_count, key_missing, doubtful_count, key_impact,
        )
        return SquadContextV2(
            team=team_ref,
            missing_players=missing_entries,
            suspended_players=suspended,
            doubtful_players=doubtful,
            missing_players_count=missing_count,
            missing_key_players_count=key_missing,
            starting_xi_confidence=clip01(xi_conf),
            line_stability_score=clip01(line_stab),
            availability_score=avail,
            key_absence_impact_score=key_impact,
        )

    confirmed_names = list((squad.confirmed_lineups or {}).get(side) or [])
    predicted_names = list((squad.predicted_lineups or {}).get(side) or [])
    bench_names = list((squad.bench or {}).get(side) or [])
    raw_missing = list((squad.missing_players_raw or {}).get(side) or [])

    confirmed_xi = _player_refs_from_names(confirmed_names, confirmed=True)
    predicted_xi = _player_refs_from_names(predicted_names, confirmed=False)
    bench = _player_refs_from_names(bench_names, confirmed=False)

    missing_entries: List[PlayerAvailabilityV2] = []
    doubtful_from_raw: List[PlayerAvailabilityV2] = []
    for entry in raw_missing:
        name, status, importance, reason, conf = _parse_player_entry(entry)
        if name == "unknown" and not reason:
            continue
        item = _availability_from_entry(name, status, importance, reason, conf)
        if status == AvailabilityStatus.DOUBTFUL:
            doubtful_from_raw.append(item)
        else:
            missing_entries.append(item)

    status_missing, suspended, doubtful = _parse_side_status_map(squad.player_status_raw or {}, side)

    news_missing, news_doubtful, _role_notes, news_suspended = _news_hints_for_side(
        news_context,
        side=side,
        home_team=home_team,
        away_team=away_team,
    )
    missing_entries = _merge_availability_lists(missing_entries, status_missing)
    missing_entries = _merge_availability_lists(missing_entries, news_missing)
    doubtful = _merge_availability_lists(doubtful, doubtful_from_raw)
    doubtful = _merge_availability_lists(doubtful, news_doubtful)
    suspended = _merge_availability_lists(suspended, news_suspended)

    missing_count = len(missing_entries)
    doubtful_count = len(doubtful)
    suspended_count = len(suspended)
    key_missing = _count_key_absences(missing_entries, suspended, doubtful)

    has_confirmed = bool(confirmed_xi)
    has_predicted = bool(predicted_xi)
    has_absence_signal = missing_count + doubtful_count + suspended_count > 0
    has_bench = bool(bench)

    if has_confirmed:
        xi_conf = 0.78
        line_stab = 0.68
        expected_xi = confirmed_xi
    elif has_predicted:
        xi_conf = 0.48
        line_stab = 0.52
        expected_xi = predicted_xi
    else:
        xi_conf = 0.2
        line_stab = 0.35
        expected_xi = []

    if has_bench and not has_confirmed:
        xi_conf = min(0.85, xi_conf + 0.05)
        line_stab = min(0.85, line_stab + 0.04)

    if has_absence_signal:
        penalty = min(0.28, 0.05 * missing_count + 0.03 * doubtful_count + 0.04 * suspended_count)
        xi_conf = max(0.12, xi_conf - penalty)
        line_stab = max(0.15, line_stab - penalty * 0.85)
        if key_missing > 0:
            xi_conf = max(0.1, xi_conf - min(0.12, 0.04 * key_missing))

    signal_confidence = 0.35
    if has_confirmed:
        signal_confidence = 0.75
    elif has_predicted:
        signal_confidence = 0.5
    elif has_absence_signal:
        signal_confidence = 0.42
    if news_context and (news_context.source_count or 0) > 0:
        signal_confidence = max(signal_confidence, clip01((news_context.confidence or 0.35) * 0.9))

    key_impact = _key_absence_impact_score(
        missing_entries,
        suspended,
        doubtful,
        signal_confidence=signal_confidence,
    )
    avail = _availability_score(
        xi_conf,
        line_stab,
        missing_count,
        key_missing,
        doubtful_count,
        key_impact,
    )

    return SquadContextV2(
        team=team_ref,
        expected_starting_xi=expected_xi,
        bench_players=bench,
        missing_players=missing_entries,
        suspended_players=suspended,
        doubtful_players=doubtful,
        missing_players_count=missing_count,
        missing_key_players_count=key_missing,
        starting_xi_confidence=clip01(xi_conf),
        line_stability_score=clip01(line_stab),
        availability_score=avail,
        key_absence_impact_score=key_impact,
    )
