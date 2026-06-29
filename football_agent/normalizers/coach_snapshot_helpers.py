"""Coach context derivation for merged live league snapshots (fail-soft, no fake history)."""

from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

from football_agent.analysis_merge.models import MergedMatchAnalysisContext
from football_agent.domain.enums_v2 import CoachTenurePhase
from football_agent.domain.models_v2 import CoachContextV2, CoachRefV2, TeamRefV2
from football_agent.news_context.models import CoachContextBlock

_FIRST_MATCH_RE = re.compile(
    r"\bfirst\s+(?:game|match|fixture)\b|\bdebut\b|\bmaiden\s+match\b|\btakes\s+charge\s+for\s+the\s+first\b",
    re.I,
)
_NEW_APPOINTMENT_RE = re.compile(
    r"\bnew(?:ly)?\s+appointed\b|\bappointed\s+(?:last|this)\s+week\b|\breplaced\b|\bsacked\b.*\bappointed\b",
    re.I,
)
_TENURE_MATCHES_RE = re.compile(
    r"(\d+)\s+(?:games?|matches?|fixtures?)\s+(?:in charge|as (?:head )?coach|since)",
    re.I,
)


def clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _resolve_coach_name(merged: MergedMatchAnalysisContext, *, side: str) -> str:
    oc_side = _openclaw_coach_side(merged, side=side)
    if oc_side is not None and oc_side.coach_name:
        name = str(oc_side.coach_name).strip()
        if name and name.lower() != "unknown":
            return name

    facts = merged.flashscore_facts
    if facts.squad_raw:
        if side == "home" and facts.squad_raw.coach_name_home:
            raw = str(facts.squad_raw.coach_name_home).strip()
            if raw and raw.lower() != "unknown":
                return raw
        if side == "away" and facts.squad_raw.coach_name_away:
            raw = str(facts.squad_raw.coach_name_away).strip()
            if raw and raw.lower() != "unknown":
                return raw
    return "Unknown"


def _openclaw_coach_side(merged: MergedMatchAnalysisContext, *, side: str):
    oc = merged.openclaw_context
    if oc is None or oc.coach_context is None:
        return None
    return oc.coach_context.home if side == "home" else oc.coach_context.away


def _brave_coach_block(merged: MergedMatchAnalysisContext) -> Optional[CoachContextBlock]:
    news = merged.news_context
    if news is None or news.coach is None:
        return None
    return news.coach


def _infer_matches_in_charge(
    *,
    days_in_charge: Optional[int],
    brave: Optional[CoachContextBlock],
    side: str,
    team_name: str,
) -> Optional[int]:
    if brave is not None:
        blob = " ".join(
            filter(
                None,
                [
                    *(brave.home_coach_recent_quotes or []),
                    *(brave.away_coach_recent_quotes or []),
                    brave.coach_h2h_recent_summary or "",
                ],
            ),
        )
        m = _TENURE_MATCHES_RE.search(blob)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

    if days_in_charge is not None and days_in_charge > 0:
        # Honest proxy: ~1 league match per 7 days, clamped
        return max(1, min(38, round(days_in_charge / 7.0)))

    oc_side = None
    return None


def _derive_tenure_flags(
    *,
    merged: MergedMatchAnalysisContext,
    matches_in_charge: Optional[int],
    days_in_charge: Optional[int],
    recent_change: bool,
    brave: Optional[CoachContextBlock],
    side: str,
) -> Tuple[bool, bool, CoachTenurePhase]:
    is_first = False
    is_bounce = False

    if matches_in_charge is not None:
        is_first = matches_in_charge <= 1
        is_bounce = 2 <= matches_in_charge <= 4
    elif days_in_charge is not None:
        is_first = days_in_charge <= 10
        is_bounce = 11 <= days_in_charge <= 35

    if recent_change and not is_first and (days_in_charge is None or days_in_charge <= 21):
        is_first = True

    if brave is not None and _use_brave_coach_hints(merged):
        quotes = list(brave.home_coach_recent_quotes or []) + list(brave.away_coach_recent_quotes or [])
        blob = "\n".join(quotes)
        if _FIRST_MATCH_RE.search(blob) or _NEW_APPOINTMENT_RE.search(blob):
            is_first = True
            is_bounce = False

    tenure = CoachTenurePhase.ESTABLISHED
    if is_first:
        tenure = CoachTenurePhase.FIRST_MATCH
    elif is_bounce:
        tenure = CoachTenurePhase.BOUNCE_WINDOW

    return is_first, is_bounce, tenure


def _coach_global_strength(
    *,
    days_in_charge: Optional[int],
    matches_in_charge: Optional[int],
    brave: Optional[CoachContextBlock],
    oc_side,
    status: str,
) -> float:
    base = 0.5
    if days_in_charge is not None:
        if days_in_charge >= 180:
            base += 0.08
        elif days_in_charge >= 60:
            base += 0.04
        elif days_in_charge <= 14:
            base -= 0.04

    if matches_in_charge is not None:
        if matches_in_charge >= 15:
            base += 0.06
        elif matches_in_charge <= 2:
            base -= 0.03

    if brave is not None and brave.coach_h2h_total_matches:
        n = int(brave.coach_h2h_total_matches)
        if n >= 5:
            base += 0.05
        elif n >= 2:
            base += 0.02

    if oc_side is not None and oc_side.influence_summary:
        base += 0.03

    if status in ("interim", "caretaker"):
        base -= 0.06
    elif status == "active":
        base += 0.02

    return clip01(base)


def _rotation_tendency(
    *,
    brave: Optional[CoachContextBlock],
    side: str,
    news_rotation_signals: list[str],
) -> float:
    score = 0.5
    if brave is not None:
        sig = (
            brave.home_coach_rotation_signal
            if side == "home"
            else brave.away_coach_rotation_signal
        )
        if sig:
            score += 0.12
        if brave.coach_priority_signal == "rotation_expected":
            score += 0.1
    if news_rotation_signals:
        score += min(0.15, 0.05 * len(news_rotation_signals))
    return clip01(score)


def _coach_vs_opponent_coach_score(brave: Optional[CoachContextBlock]) -> float:
    if brave is None:
        return 0.5
    stat = brave.stat
    total = stat.coach_h2h_total_matches or brave.coach_h2h_total_matches
    if not total:
        return 0.5
    total_n = max(1, int(total))
    hw = int(stat.coach_h2h_home_wins or brave.coach_h2h_home_wins or 0)
    aw = int(stat.coach_h2h_away_wins or brave.coach_h2h_away_wins or 0)
    if hw + aw == 0:
        return 0.5
    edge = (hw - aw) / (hw + aw)
    return clip01(0.5 + edge * 0.25)


def _coach_start_date(days_in_charge: Optional[int], kickoff: Optional[date]) -> Optional[date]:
    if days_in_charge is None or kickoff is None or days_in_charge < 0:
        return None
    try:
        from datetime import timedelta

        return kickoff - timedelta(days=days_in_charge)
    except Exception:
        return None


def _use_brave_coach_hints(merged: MergedMatchAnalysisContext) -> bool:
    from football_agent.services.openclaw_primary_enrichment import (
        brave_fallback_allowed,
        openclaw_primary_enrichment,
    )

    if openclaw_primary_enrichment() and merged.openclaw_context is not None:
        return brave_fallback_allowed()
    return merged.news_context is not None


def coach_context_from_merged(
    merged: MergedMatchAnalysisContext,
    team_ref: TeamRefV2,
    *,
    side: str,
) -> CoachContextV2:
    name = _resolve_coach_name(merged, side=side)
    brave = _brave_coach_block(merged) if _use_brave_coach_hints(merged) else None
    oc_side = _openclaw_coach_side(merged, side=side)

    days_in_charge: Optional[int] = None
    status = "unknown"

    if oc_side is not None:
        if oc_side.coach_name and name in ("Unknown", ""):
            name = str(oc_side.coach_name)
        from football_agent.openclaw_context.snapshot_mapper import coach_tenure_from_openclaw

        oc_days, oc_first, _recent = coach_tenure_from_openclaw(oc_side)
        if oc_days is not None:
            days_in_charge = oc_days
        if oc_side.pressure_summary and "interim" in oc_side.pressure_summary.lower():
            status = "interim"

    if brave is not None and days_in_charge is None:
        if side == "home":
            news_name = brave.news.home_coach_name or brave.home_coach_name
            if news_name and name in ("Unknown", ""):
                name = news_name
            days_in_charge = brave.stat.home_coach_tenure_days or brave.home_coach_tenure_days
            status = str(brave.news.home_coach_status or brave.home_coach_status or "unknown")
        else:
            news_name = brave.news.away_coach_name or brave.away_coach_name
            if news_name and name in ("Unknown", ""):
                name = news_name
            days_in_charge = brave.stat.away_coach_tenure_days or brave.away_coach_tenure_days
            status = str(brave.news.away_coach_status or brave.away_coach_status or "unknown")

    if oc_side is not None and days_in_charge is None and oc_side.tenure_summary:
        m = re.search(r"(\d+)\s+days?", oc_side.tenure_summary, re.I)
        if m:
            days_in_charge = int(m.group(1))

    recent_change = bool(oc_side and oc_side.recent_change_flag)
    matches_in_charge = _infer_matches_in_charge(
        days_in_charge=days_in_charge,
        brave=brave,
        side=side,
        team_name=team_ref.name,
    )

    is_first, is_bounce, tenure = _derive_tenure_flags(
        merged=merged,
        matches_in_charge=matches_in_charge,
        days_in_charge=days_in_charge,
        recent_change=recent_change,
        brave=brave,
        side=side,
    )

    rotation_signals: list[str] = []
    if merged.openclaw_context and merged.openclaw_context.squad_context:
        oc_sq = merged.openclaw_context.squad_context
        side_sq = oc_sq.home if side == "home" else oc_sq.away
        rotation_signals = list(side_sq.expected_rotation_notes or [])
    if merged.news_context and merged.news_context.general_news and _use_brave_coach_hints(merged):
        rotation_signals.extend(list(merged.news_context.general_news.predicted_lineup_signals or []))

    global_strength = _coach_global_strength(
        days_in_charge=days_in_charge,
        matches_in_charge=matches_in_charge,
        brave=brave,
        oc_side=oc_side,
        status=status,
    )

    profile = (brave.profile_home if side == "home" else brave.profile_away) if brave else None
    if profile is not None and profile.coach_global_strength_score:
        global_strength = clip01(
            0.65 * global_strength + 0.35 * float(profile.coach_global_strength_score),
        )

    previous_teams = list(profile.previous_teams or []) if profile else []

    kickoff_date = None
    if merged.flashscore_facts.meta.kickoff_utc:
        kickoff_date = merged.flashscore_facts.meta.kickoff_utc.date()

    return CoachContextV2(
        coach=CoachRefV2(name=name or "Unknown"),
        team=team_ref,
        coach_start_date=_coach_start_date(days_in_charge, kickoff_date),
        days_in_charge=days_in_charge,
        matches_in_charge=matches_in_charge,
        tenure_phase=tenure,
        is_first_match=is_first,
        is_new_coach_bounce_window=is_bounce,
        coach_global_strength_score=global_strength,
        coach_vs_opponent_team_score=global_strength,
        coach_vs_opponent_coach_score=_coach_vs_opponent_coach_score(brave),
        previous_teams=previous_teams,
        coach_rotation_tendency_score=_rotation_tendency(
            brave=brave,
            side=side,
            news_rotation_signals=rotation_signals,
        ),
    )


def coaches_confidence_from_context(
    merged: MergedMatchAnalysisContext,
    home_coach: CoachContextV2,
    away_coach: CoachContextV2,
) -> float:
    """Block confidence for ConfidenceBreakdownV2.coaches_confidence."""
    conf = 0.18
    brave = _brave_coach_block(merged)

    named = sum(
        1
        for c in (home_coach, away_coach)
        if c.coach.name and c.coach.name != "Unknown"
    )
    if named == 2:
        conf = 0.58
    elif named == 1:
        conf = 0.42

    if brave is not None and brave.coach_news_confidence:
        conf = max(conf, clip01(brave.coach_news_confidence))
    if brave is not None and brave.news and brave.news.coach_news_confidence:
        conf = max(conf, clip01(brave.news.coach_news_confidence))

    tenure_signals = sum(
        1
        for c in (home_coach, away_coach)
        if c.days_in_charge is not None or c.matches_in_charge is not None
    )
    if tenure_signals:
        conf = max(conf, 0.45 + 0.06 * tenure_signals)

    if home_coach.is_first_match or away_coach.is_first_match:
        conf = max(conf, 0.48)
    if home_coach.is_new_coach_bounce_window or away_coach.is_new_coach_bounce_window:
        conf = max(conf, 0.46)

    oc = merged.openclaw_context
    if oc and oc.coach_context:
        if oc.coach_context.home.coach_name or oc.coach_context.away.coach_name:
            conf = max(conf, 0.5)

    if named == 0 and tenure_signals == 0:
        return 0.18
    return clip01(conf)
