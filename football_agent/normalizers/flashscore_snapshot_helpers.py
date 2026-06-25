"""Flashscore → MatchAnalysisSnapshotV2 mapping helpers (live merged path)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List, Optional, Tuple

from football_agent.analysis_merge.models import MergedMatchAnalysisContext
from football_agent.collectors.confidence import (
    bundle_overall_confidence,
    clamp_confidence,
    form_confidence,
    match_meta_confidence,
    odds_confidence,
    standings_confidence,
)
from football_agent.domain.enums_v2 import MathematicalGoalStatus, MotivationContext
from football_agent.domain.models_v2 import (
    CoachContextV2,
    CoachRefV2,
    ConfidenceBreakdownV2,
    H2HContextV2,
    OddsContextV2,
    ScheduleContextV2,
    SquadContextV2,
    TeamFormBlockV2,
    TeamMotivationBlockV2,
    TeamRefV2,
    TeamScheduleMiniBlockV2,
)
from football_agent.flashscore.derived_season import LeagueTableMotivationContext
from football_agent.flashscore.models import (
    FlashscoreFormBlock,
    FlashscoreMatchFacts,
    FlashscoreScheduleRaw,
    FlashscoreSquadRaw,
    FlashscoreStandings,
    FlashscoreTeamFormBlock,
)
from football_agent.flashscore.raw_enrich import schedule_has_signal, squad_has_signal

_URGENCY_TO_SCORE = {
    "LOW": 0.4,
    "MEDIUM": 0.55,
    "HIGH": 0.7,
    "CRITICAL": 0.85,
    "UNKNOWN": 0.5,
}

_BAND_TO_MOTIVATION = {
    "TITLE": MotivationContext.TITLE_RACE,
    "EUROPE": MotivationContext.EURO_RACE,
    "MIDTABLE": MotivationContext.MIDTABLE_NEUTRAL,
    "RELEGATION": MotivationContext.RELEGATION_BATTLE,
}


def clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _pts_rate(results: List[str], n: int = 5) -> float:
    if not results:
        return 0.5
    window = results[:n]
    pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in window)
    return pts / (3.0 * len(window))


def form_block_from_flashscore(
    form: Optional[FlashscoreFormBlock],
    *,
    side: str,
) -> TeamFormBlockV2:
    if form is None:
        return TeamFormBlockV2()

    team_block: Optional[FlashscoreTeamFormBlock] = form.home if side == "home" else form.away
    if team_block is None:
        return TeamFormBlockV2()

    results = list(team_block.last_n_results or [])
    if not results:
        return TeamFormBlockV2()

    last5 = _pts_rate(results, 5)
    last10 = _pts_rate(results, 10)
    trend = last5 - last10 if len(results) >= 3 else 0.0

    home_results = list(team_block.home_only_form or [])
    away_results = list(team_block.away_only_form or [])

    return TeamFormBlockV2(
        last_5_form_score=clip01(last5),
        last_10_form_score=clip01(last10),
        home_form_score=clip01(_pts_rate(home_results, 5)) if home_results else 0.5,
        away_form_score=clip01(_pts_rate(away_results, 5)) if away_results else 0.5,
        form_under_current_coach=clip01(last10),
        performance_trend_score=max(-1.0, min(1.0, trend * 2.0)),
    )


def motivation_block_from_derived(
    derived: LeagueTableMotivationContext,
    standings: Optional[FlashscoreStandings],
    *,
    side: str,
) -> TeamMotivationBlockV2:
    if standings is None:
        return TeamMotivationBlockV2(derivation_warnings=list(derived.derivation_warnings or []))

    is_home = side == "home"
    pos = standings.home_position if is_home else standings.away_position
    pts = standings.home_points if is_home else standings.away_points
    gd = standings.home_goal_difference if is_home else standings.away_goal_difference

    band = derived.home_target_band if is_home else derived.away_target_band
    urgency = derived.urgency_level_home if is_home else derived.urgency_level_away
    motivation_ctx = _BAND_TO_MOTIVATION.get(band)

    gap_up, gap_down = _points_gaps_for_side(derived, side=side, band=band)

    return TeamMotivationBlockV2(
        derivation_warnings=list(derived.derivation_warnings or []),
        motivation_context=motivation_ctx,
        motivation_score=clip01(_URGENCY_TO_SCORE.get(urgency or "UNKNOWN", 0.5)),
        mathematical_goal_status=_math_goal_status(derived, side=side, band=band),
        league_position=pos,
        points=pts,
        goal_difference=gd,
        points_gap_to_target_up=gap_up,
        points_gap_to_target_down=gap_down,
    )


def _points_gaps_for_side(
    derived: LeagueTableMotivationContext,
    *,
    side: str,
    band: str,
) -> Tuple[Optional[int], Optional[int]]:
    if side == "home":
        if band == "TITLE":
            return derived.gap_to_title_points, None
        if band == "EUROPE":
            return derived.gap_to_europe_points, None
        if band == "RELEGATION":
            return None, derived.gap_to_relegation_safety_points
        return derived.aux_gap_to_title_positions, derived.aux_gap_to_relegation_line_positions

    if band == "TITLE":
        return derived.points_gap_away_to_title, None
    if band == "EUROPE":
        return derived.points_gap_away_to_europe, None
    if band == "RELEGATION":
        return None, derived.points_gap_away_to_relegation_line
    return derived.points_gap_away_to_title, derived.points_gap_away_to_relegation_line


def _math_goal_status(
    derived: LeagueTableMotivationContext,
    *,
    side: str,
    band: str,
) -> Optional[MathematicalGoalStatus]:
    if band == "UNKNOWN":
        return None

    is_home = side == "home"
    title_alive = derived.home_mathematical_title_alive if is_home else derived.away_mathematical_title_alive
    euro_alive = derived.home_mathematical_europe_alive if is_home else derived.away_mathematical_europe_alive
    releg_alive = (
        derived.home_mathematical_relegation_risk_alive
        if is_home
        else derived.away_mathematical_relegation_risk_alive
    )

    if band == "TITLE" and title_alive is False:
        return MathematicalGoalStatus.ELIMINATED
    if band == "RELEGATION" and releg_alive is False:
        return MathematicalGoalStatus.SECURED
    if band == "MIDTABLE" and title_alive is False and euro_alive is False and releg_alive is False:
        return MathematicalGoalStatus.NEUTRAL
    if band in ("TITLE", "EUROPE", "RELEGATION"):
        return MathematicalGoalStatus.ACHIEVABLE
    return MathematicalGoalStatus.NEUTRAL


def schedule_mini_from_raw(
    schedule: Optional[FlashscoreScheduleRaw],
    kickoff: datetime,
    *,
    side: str,
) -> TeamScheduleMiniBlockV2:
    ctx = schedule_context_from_raw(schedule, kickoff, side=side)
    if ctx.days_since_last_match is None and ctx.matches_last_14_days == 0:
        return TeamScheduleMiniBlockV2()
    return TeamScheduleMiniBlockV2(
        fixture_congestion_score=ctx.fixture_congestion_score,
        rotation_risk_score=ctx.rotation_risk_score,
        pre_big_match_preservation_risk=ctx.pre_big_match_preservation_risk,
        post_big_match_relaxation_risk=ctx.post_big_match_relaxation_risk,
    )


def schedule_context_from_raw(
    schedule: Optional[FlashscoreScheduleRaw],
    kickoff: datetime,
    *,
    side: str,
    rotation_hint: float = 0.0,
) -> ScheduleContextV2:
    team_ref = TeamRefV2(team_id=0, name=side, short_name=side)
    if schedule is None:
        return ScheduleContextV2(team=team_ref)

    ref = kickoff.date() if isinstance(kickoff, datetime) else kickoff
    prev_date = schedule.previous_match_date_home if side == "home" else schedule.previous_match_date_away
    next_date = schedule.next_match_date_home if side == "home" else schedule.next_match_date_away
    recent_dates = (
        list(schedule.recent_match_dates_home or [])
        if side == "home"
        else list(schedule.recent_match_dates_away or [])
    )

    days_since = (ref - prev_date).days if prev_date else None
    days_to = (next_date - ref).days if next_date else None
    if days_to is not None and days_to < 0:
        # Next fixture date is before/at kickoff (stale GraphQL "next" or TZ drift).
        days_to = None

    matches_14 = sum(1 for d in recent_dates if 0 <= (ref - d).days <= 14)
    if prev_date and prev_date not in recent_dates:
        if 0 <= (ref - prev_date).days <= 14:
            matches_14 = max(matches_14, 1)

    matches_next_7 = 1 if next_date and 0 < (next_date - ref).days <= 7 else 0
    congestion = clip01(matches_14 / 5.0)
    rotation = clip01(max(congestion * 0.6, rotation_hint))

    pre_big = 0.3 if days_to is not None and 0 < days_to <= 3 else 0.0
    post_relax = 0.2 if days_since is not None and 0 <= days_since <= 2 else 0.0

    return ScheduleContextV2(
        team=team_ref,
        days_since_last_match=days_since,
        days_to_next_match=days_to,
        matches_last_14_days=matches_14,
        matches_next_7_days=matches_next_7,
        fixture_congestion_score=congestion,
        rotation_risk_score=rotation,
        pre_big_match_preservation_risk=pre_big,
        post_big_match_relaxation_risk=post_relax,
    )


from football_agent.normalizers.coach_snapshot_helpers import (
    coach_context_from_merged,
    coaches_confidence_from_context,
)
from football_agent.normalizers.squad_snapshot_helpers import squad_context_from_raw


def _coach_name_for_conf(
    merged: MergedMatchAnalysisContext,
    *,
    side: str,
    coach_ctx: Optional[CoachContextV2],
) -> str:
    if coach_ctx is not None and coach_ctx.coach.name and coach_ctx.coach.name != "Unknown":
        return coach_ctx.coach.name
    from football_agent.normalizers.coach_snapshot_helpers import _resolve_coach_name

    return _resolve_coach_name(merged, side=side)


def h2h_context_from_flashscore(h2h) -> H2HContextV2:  # noqa: ANN001
    if not h2h:
        return H2HContextV2()

    total = int(h2h.recent_h2h_matches or 0)
    hw = int(h2h.home_h2h_wins or 0)
    aw = int(h2h.away_h2h_wins or 0)
    recent_score = 0.0
    if total > 0 and (hw + aw) > 0:
        recent_score = (hw - aw) / max(hw + aw, 1)
        recent_score = max(-1.0, min(1.0, recent_score))

    return H2HContextV2(
        team_h2h_total_matches=total,
        team_h2h_recent_score=recent_score,
        team_h2h_home_away_split=recent_score * 0.5,
        h2h_btts_rate=float(h2h.btts_h2h_rate) if h2h.btts_h2h_rate is not None else 0.5,
        h2h_over25_rate=0.5,
        h2h_context_bias=recent_score * 0.3,
    )


def news_rotation_hint(merged: MergedMatchAnalysisContext) -> float:
    brave = merged.news_context
    if brave is None or brave.general_news is None:
        return 0.0
    signals = list(brave.general_news.predicted_lineup_signals or [])
    fatigue = list(brave.general_news.schedule_pressure_signals or [])
    if not signals and not fatigue:
        return 0.0
    return clip01(0.15 + 0.1 * min(3, len(signals) + len(fatigue)))


def confidence_breakdown_from_merged(
    merged: MergedMatchAnalysisContext,
    odds_ctx: OddsContextV2,
    *,
    home_coach: Optional[CoachContextV2] = None,
    away_coach: Optional[CoachContextV2] = None,
) -> ConfidenceBreakdownV2:
    facts = merged.flashscore_facts
    meta = facts.meta

    meta_conf, _, _ = match_meta_confidence(
        home_team=meta.home_team_name or "",
        away_team=meta.away_team_name or "",
        competition_name=meta.competition_name or "",
        kickoff_present=meta.kickoff_utc is not None,
        venue_present=False,
        round_present=bool(meta.round),
        competition_valid=bool(meta.competition_name),
        teams_valid=bool(meta.home_team_name and meta.away_team_name),
    )

    teams_conf = 0.15
    if facts.standings:
        teams_conf, _, _ = standings_confidence(facts.standings.model_dump())

    form_conf = 0.15
    if facts.form:
        payload = {
            "home": facts.form.home.model_dump() if facts.form.home else {},
            "away": facts.form.away.model_dump() if facts.form.away else {},
        }
        form_conf, _, _ = form_confidence(payload)

    squads_conf = 0.15
    if facts.squad_raw:
        sq = facts.squad_raw
        confirmed = any((sq.confirmed_lineups or {}).get(s) for s in ("home", "away"))
        predicted = any((sq.predicted_lineups or {}).get(s) for s in ("home", "away"))
        missing = any((sq.missing_players_raw or {}).get(s) for s in ("home", "away"))
        status_map = sq.player_status_raw or {}
        has_status = any(isinstance(v, dict) and v for v in status_map.values())
        has_bench = any((sq.bench or {}).get(s) for s in ("home", "away"))
        if confirmed:
            squads_conf = 0.72
        elif predicted:
            squads_conf = 0.48
        elif missing or has_status:
            squads_conf = 0.38
        elif has_bench:
            squads_conf = 0.3
        elif squad_has_signal(sq.model_dump()):
            squads_conf = 0.25
        if merged.news_context and merged.news_context.general_news:
            gn = merged.news_context.general_news
            if gn.injuries_signals or gn.suspension_signals or gn.predicted_lineup_signals:
                squads_conf = max(squads_conf, clamp_confidence(0.32 + (merged.news_context.confidence or 0.0) * 0.35))

    home_coach_name = _coach_name_for_conf(merged, side="home", coach_ctx=home_coach)
    away_coach_name = _coach_name_for_conf(merged, side="away", coach_ctx=away_coach)
    coaches_conf = 0.2
    if home_coach is not None and away_coach is not None:
        coaches_conf = coaches_confidence_from_context(merged, home_coach, away_coach)
    elif home_coach_name != "Unknown" and away_coach_name != "Unknown":
        coaches_conf = 0.55
    elif home_coach_name != "Unknown" or away_coach_name != "Unknown":
        coaches_conf = 0.4
    if merged.news_context and merged.news_context.coach:
        cb = merged.news_context.coach
        if cb.coach_news_confidence:
            coaches_conf = max(coaches_conf, clamp_confidence(cb.coach_news_confidence))

    odds_conf = odds_ctx.odds_confidence if odds_ctx else 0.15
    if merged.odds_context is not None:
        mk = merged.odds_context.markets
        markets_payload: dict[str, Any] = {}
        for field_name in (
            "home_win",
            "away_win",
            "double_chance_1x",
            "double_chance_x2",
            "btts_yes",
            "over_1_5",
        ):
            q = getattr(mk, field_name, None)
            if q is not None:
                markets_payload[field_name.upper()] = {"odds_value": q.odds_value}
        oc, _, _ = odds_confidence({"markets": markets_payload, "market_count": len(markets_payload)})
        odds_conf = max(odds_conf, oc)

    news_conf = 0.15
    if merged.news_context is not None:
        if merged.news_context.source_count:
            news_conf = clamp_confidence(merged.news_context.confidence or 0.35)
        elif merged.openclaw_context and merged.openclaw_context.news:
            news_conf = 0.35

    schedule_conf = 0.15
    if facts.schedule_raw and schedule_has_signal(facts.schedule_raw.model_dump()):
        sched = facts.schedule_raw
        filled = sum(
            1
            for v in (
                sched.previous_match_date_home,
                sched.previous_match_date_away,
                sched.next_match_date_home,
                sched.next_match_date_away,
            )
            if v
        )
        filled += min(2, len(sched.recent_match_dates_home or []) + len(sched.recent_match_dates_away or []))
        schedule_conf = clamp_confidence(0.25 + filled * 0.12)

    h2h_conf = 0.15
    if facts.h2h:
        n = int(facts.h2h.recent_h2h_matches or 0)
        if n >= 5:
            h2h_conf = 0.75
        elif n >= 3:
            h2h_conf = 0.55
        elif n > 0:
            h2h_conf = 0.35

    block_scores = [
        meta_conf,
        teams_conf,
        squads_conf,
        coaches_conf,
        odds_conf,
        news_conf,
        schedule_conf,
        h2h_conf,
    ]
    completeness = sum(block_scores) / len(block_scores)

    collector_blocks = {
        "match_meta": meta_conf,
        "teams": teams_conf,
        "form": form_conf,
        "odds": odds_conf,
    }
    collector_overall = bundle_overall_confidence(collector_blocks)
    overall = clip01(completeness * 0.55 + collector_overall * 0.45)

    freshness = 0.45
    collected_at = facts.provenance.collected_at_utc
    if collected_at is not None:
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - collected_at).total_seconds() / 3600.0
        if age_hours <= 6:
            freshness = 0.9
        elif age_hours <= 24:
            freshness = 0.75
        elif age_hours <= 72:
            freshness = 0.55

    source_count = 1
    if merged.odds_context is not None:
        source_count += 1
    if merged.openclaw_context is not None:
        source_count += 1
    if merged.news_context is not None and (merged.news_context.source_count or 0) > 0:
        source_count += 1
    agreement = 0.45 if source_count <= 1 else clip01(0.5 + 0.12 * (source_count - 1))

    return ConfidenceBreakdownV2(
        match_meta_confidence=meta_conf,
        teams_confidence=teams_conf,
        squads_confidence=squads_conf,
        coaches_confidence=coaches_conf,
        odds_confidence=odds_conf,
        news_confidence=news_conf,
        schedule_confidence=schedule_conf,
        h2h_confidence=h2h_conf,
        data_freshness_score=freshness,
        source_agreement_score=agreement,
        overall_completeness_score=clip01(completeness),
        overall_confidence_score=overall,
    )


def attach_schedule_team_ref(schedule: ScheduleContextV2, team_ref: TeamRefV2) -> ScheduleContextV2:
    return schedule.model_copy(update={"team": team_ref})
