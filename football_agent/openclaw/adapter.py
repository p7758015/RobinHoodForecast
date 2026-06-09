"""
Maps OpenClawMatchPayload → MatchAnalysisSnapshotV2.

No scorer logic: only structural normalization and safe defaults.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Callable, List, Optional

from football_agent.domain.enums_v2 import (
    AvailabilityStatus,
    CoachTenurePhase,
    MatchImportance,
    NewsSeverity,
    OpponentStrengthBand,
    PlayerImportance,
    SeasonPhase,
    TournamentType,
)
from football_agent.domain.models_v2 import (
    CoachContextV2,
    CoachRefV2,
    CompetitionRefV2,
    ConfidenceBreakdownV2,
    H2HContextV2,
    MatchAnalysisSnapshotV2,
    MatchMetaV2,
    NewsContextV2,
    NewsItemV2,
    OddsContextV2,
    OddsMarketV2,
    PlayerAvailabilityV2,
    PlayerRefV2,
    ScheduleContextV2,
    ScheduleMatchContextV2,
    SquadContextV2,
    TeamContextV2,
    TeamFormBlockV2,
    TeamMotivationBlockV2,
    TeamRefV2,
    TeamScheduleMiniBlockV2,
)

from football_agent.openclaw.models import (
    OpenClawCoachBlock,
    OpenClawH2HBlock,
    OpenClawMatchPayload,
    OpenClawNewsBlock,
    OpenClawOddsMarkets,
    OpenClawPlayerAvailability,
    OpenClawScheduleBlock,
    OpenClawScheduleMatchStub,
    OpenClawSquadBlock,
    OpenClawTeamContextBlock,
    OpenClawTeamRef,
)

logger = logging.getLogger(__name__)


def _clip(v: Optional[float], lo: float, hi: float, default: float) -> float:
    if v is None:
        return default
    return max(lo, min(hi, float(v)))


def _stable_match_id(seed: str) -> int:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(h[:15], 16) % (2**31 - 1) or 1


def _stable_team_id(ref: OpenClawTeamRef) -> int:
    if ref.team_id is not None:
        return int(ref.team_id)
    key = f"{ref.name}|{ref.short_name or ''}|{ref.country or ''}"
    return _stable_match_id("team:" + key)


def _parse_form_from_string(form: Optional[str]) -> Optional[float]:
    if not form:
        return None
    chunk = form.replace(",", "").replace(" ", "").upper()[-10:]
    if not chunk:
        return None
    wins = chunk.count("W")
    draws = chunk.count("D")
    losses = chunk.count("L")
    total = wins + draws + losses
    if total == 0:
        return None
    return (wins * 1.0 + draws * 0.5) / total


def _season_phase_from_round(r: Optional[int], total: int = 38) -> Optional[SeasonPhase]:
    if r is None or total <= 0:
        return None
    p = r / total
    if p < 0.25:
        return SeasonPhase.EARLY
    if p < 0.65:
        return SeasonPhase.MID
    if p < 0.85:
        return SeasonPhase.LATE
    return SeasonPhase.FINAL_RUN_IN


def _coach_tenure(block: Optional[OpenClawCoachBlock]) -> CoachTenurePhase:
    if not block:
        return CoachTenurePhase.ESTABLISHED
    if block.tenure_phase_hint:
        return CoachTenurePhase(block.tenure_phase_hint)
    if block.is_first_match:
        return CoachTenurePhase.FIRST_MATCH
    if block.is_new_coach_bounce_window:
        return CoachTenurePhase.BOUNCE_WINDOW
    return CoachTenurePhase.ESTABLISHED


def _map_availability(a: OpenClawPlayerAvailability) -> PlayerAvailabilityV2:
    try:
        status = AvailabilityStatus(a.status)
    except ValueError:
        status = AvailabilityStatus.UNKNOWN
    try:
        importance = PlayerImportance(a.importance)
    except ValueError:
        importance = PlayerImportance.MEDIUM
    conf = a.confidence if a.confidence is not None else 0.5
    return PlayerAvailabilityV2(
        player=PlayerRefV2(
            player_id=a.player.player_id,
            name=a.player.name or "Unknown",
            position=a.player.position,
            shirt_number=a.player.shirt_number,
        ),
        status=status,
        importance=importance,
        reason=a.reason,
        expected_return_date=a.expected_return_date,
        confidence=_clip(conf, 0.0, 1.0, 0.5),
    )


def _odds_market(key: str, market_name: str, selection: str, val: Optional[float]) -> Optional[OddsMarketV2]:
    if val is None or val <= 1.0:
        return None
    return OddsMarketV2(
        market_key=key,
        market_name=market_name,
        selection_name=selection,
        odds=float(val),
        source="openclaw",
    )


def _build_odds_ctx(o: Optional[OpenClawOddsMarkets]) -> OddsContextV2:
    if not o:
        return OddsContextV2(odds_confidence=0.15)
    home = _odds_market("HOME_WIN", "Match Winner", "Home", o.home_win)
    draw = _odds_market("DRAW", "Match Winner", "Draw", o.draw)
    away = _odds_market("AWAY_WIN", "Match Winner", "Away", o.away_win)
    hnl = _odds_market("HOME_NOT_LOSE", "Double Chance", "Home/Draw", o.home_not_lose)
    anl = _odds_market("AWAY_NOT_LOSE", "Double Chance", "Draw/Away", o.away_not_lose)
    btts = _odds_market("BTTS_YES", "Both Teams Score", "Yes", o.btts_yes)
    hts = _odds_market("HOME_TEAM_TO_SCORE", "Home Team To Score", "Yes", o.home_team_to_score)
    ats = _odds_market("AWAY_TEAM_TO_SCORE", "Away Team To Score", "Yes", o.away_team_to_score)
    o15 = _odds_market("OVER_1_5", "Goals Over/Under", "Over 1.5", o.over_15)
    filled = sum(
        1
        for x in (home, draw, away, hnl, anl, btts, hts, ats, o15)
        if x is not None
    )
    odds_conf = _clip(0.3 + filled / 9.0 * 0.7, 0.0, 1.0, 0.3)
    return OddsContextV2(
        home_win=home,
        draw=draw,
        away_win=away,
        home_not_lose=hnl,
        away_not_lose=anl,
        btts_yes=btts,
        home_team_to_score=hts,
        away_team_to_score=ats,
        over_15=o15,
        odds_confidence=odds_conf,
    )


def _team_ref(ref: OpenClawTeamRef) -> TeamRefV2:
    tid = _stable_team_id(ref)
    name = ref.name.strip() if ref.name else f"Team_{tid}"
    return TeamRefV2(
        team_id=tid,
        name=name,
        short_name=ref.short_name or name[:40],
        country=ref.country,
    )


def _mini_schedule_team(ctx: Optional[OpenClawTeamContextBlock]) -> TeamScheduleMiniBlockV2:
    if not ctx or not ctx.mini_schedule:
        return TeamScheduleMiniBlockV2()
    m = ctx.mini_schedule
    return TeamScheduleMiniBlockV2(
        fixture_congestion_score=_clip(m.fixture_congestion_score, 0.0, 1.0, 0.0),
        rotation_risk_score=_clip(m.rotation_risk_score, 0.0, 1.0, 0.0),
        pre_big_match_preservation_risk=_clip(m.pre_big_match_preservation_risk, 0.0, 1.0, 0.0),
        post_big_match_relaxation_risk=_clip(m.post_big_match_relaxation_risk, 0.0, 1.0, 0.0),
    )


def _team_form(ctx: Optional[OpenClawTeamContextBlock]) -> TeamFormBlockV2:
    if not ctx:
        return TeamFormBlockV2()

    inferred = _parse_form_from_string(ctx.table.form_string if ctx.table else None)

    if not ctx.form:
        if inferred is not None:
            return TeamFormBlockV2(
                last_5_form_score=inferred,
                last_10_form_score=inferred,
                home_form_score=inferred,
                away_form_score=inferred,
            )
        return TeamFormBlockV2()

    f = ctx.form

    lf5_val = TeamFormBlockV2().last_5_form_score
    lf10_val = TeamFormBlockV2().last_10_form_score
    hf_val = TeamFormBlockV2().home_form_score
    af_val = TeamFormBlockV2().away_form_score
    if f.last_5_form_score is not None:
        lf5_val = _clip(f.last_5_form_score, 0.0, 1.0, 0.5)
    if f.last_10_form_score is not None:
        lf10_val = _clip(f.last_10_form_score, 0.0, 1.0, 0.5)
    if f.home_form_score is not None:
        hf_val = _clip(f.home_form_score, 0.0, 1.0, 0.5)
    if f.away_form_score is not None:
        af_val = _clip(f.away_form_score, 0.0, 1.0, 0.5)
    uc_val = (
        TeamFormBlockV2().form_under_current_coach
        if f.form_under_coach_score is None
        else _clip(f.form_under_coach_score, 0.0, 1.0, 0.5)
    )
    tr_val = (
        TeamFormBlockV2().performance_trend_score
        if f.performance_trend_score is None
        else _clip(f.performance_trend_score, -1.0, 1.0, 0.0)
    )

    default_form = lf5_val == 0.5 and f.last_5_form_score is None
    if default_form and inferred is not None:
        lf5_val = lf10_val = hf_val = af_val = inferred

    return TeamFormBlockV2(
        last_5_form_score=lf5_val,
        last_10_form_score=lf10_val,
        home_form_score=hf_val,
        away_form_score=af_val,
        form_under_current_coach=uc_val,
        performance_trend_score=tr_val,
    )


def _team_motivation(ctx: Optional[OpenClawTeamContextBlock]) -> TeamMotivationBlockV2:
    block = TeamMotivationBlockV2(motivation_score=0.5)
    if not ctx:
        return block
    if ctx.motivation_score is not None:
        block.motivation_score = _clip(ctx.motivation_score, 0.0, 1.0, 0.5)
    if ctx.table:
        t = ctx.table
        block.league_position = t.position
        block.points = t.points
        block.goal_difference = t.goal_difference
        block.points_gap_to_target_up = t.gap_points_above_target
        block.points_gap_to_target_down = t.gap_points_below_target
    return block


def _team_ctx(ref: Optional[OpenClawTeamRef], block: Optional[OpenClawTeamContextBlock]) -> TeamContextV2:
    """Merge standalone team ref with context.team (collector may populate either)."""
    if block is None:
        team_ref = ref or OpenClawTeamRef(name="Unknown")
        block = OpenClawTeamContextBlock(team=team_ref)
    merged_name = (block.team.name or "").strip()
    effective_ref = block.team if merged_name else (ref or block.team or OpenClawTeamRef(name="Unknown"))
    if ref and ref.name.strip() and not (block.team.name or "").strip():
        effective_ref = ref
    tr = _team_ref(effective_ref)
    return TeamContextV2(
        team=tr,
        baseline_strength_score=_clip(block.baseline_strength_score, 0.0, 1.0, 0.5),
        form=_team_form(block),
        motivation=_team_motivation(block),
        schedule=_mini_schedule_team(block),
        availability_score=_clip(block.availability_score, 0.0, 1.0, 0.5),
        bench_quality_score=_clip(block.bench_quality_score, 0.0, 1.0, 0.5),
        line_stability_score=_clip(block.line_stability_score, 0.0, 1.0, 0.5),
    )


def _squad(team: TeamRefV2, home: Optional[OpenClawSquadBlock]) -> SquadContextV2:
    if not home:
        return SquadContextV2(team=team)
    starters = [
        PlayerRefV2(player_id=p.player_id, name=p.name or "?", position=p.position, shirt_number=p.shirt_number)
        for p in home.expected_starting_xi
    ]
    bench = [
        PlayerRefV2(player_id=p.player_id, name=p.name or "?", position=p.position, shirt_number=p.shirt_number)
        for p in home.bench_players
    ]
    missing: List[PlayerAvailabilityV2] = [_map_availability(x) for x in home.unavailable]
    doubtful: List[PlayerAvailabilityV2] = [_map_availability(x) for x in home.doubtful]
    suspended: List[PlayerAvailabilityV2] = [_map_availability(x) for x in home.suspended]

    missing_key = sum(
        1 for x in home.unavailable if x.importance in ("HIGH", "CRITICAL")
    ) + sum(1 for x in home.suspended if x.importance in ("HIGH", "CRITICAL"))
    xi_conf = home.starting_xi_confidence
    line_stab = home.line_stability_score
    return SquadContextV2(
        team=team,
        expected_starting_xi=starters,
        bench_players=bench,
        missing_players=missing,
        suspended_players=suspended,
        doubtful_players=doubtful,
        missing_players_count=len(home.unavailable),
        missing_key_players_count=missing_key,
        starting_xi_confidence=_clip(xi_conf, 0.0, 1.0, 0.5),
        line_stability_score=_clip(line_stab, 0.0, 1.0, 0.5),
    )


def _coach(team: TeamRefV2, block: Optional[OpenClawCoachBlock]) -> CoachContextV2:
    if not block or not block.name:
        dummy = CoachRefV2(coach_id=None, name="Unknown")
        return CoachContextV2(coach=dummy, team=team)
    ref = CoachRefV2(coach_id=block.coach_id, name=block.name, nationality=block.nationality)
    return CoachContextV2(
        coach=ref,
        team=team,
        coach_start_date=block.coach_start_date,
        days_in_charge=block.days_in_charge,
        matches_in_charge=block.matches_in_charge,
        tenure_phase=_coach_tenure(block),
        is_first_match=block.is_first_match,
        is_new_coach_bounce_window=block.is_new_coach_bounce_window,
        coach_global_strength_score=_clip(block.coach_global_strength_score, 0.0, 1.0, 0.5),
        coach_rotation_tendency_score=_clip(block.coach_rotation_tendency_score, 0.0, 1.0, 0.5),
    )


def _parse_match_importance(s: Optional[str]) -> MatchImportance:
    if not s:
        return MatchImportance.MEDIUM
    try:
        return MatchImportance(s.upper())
    except ValueError:
        return MatchImportance.MEDIUM


def _schedule_stub_to_ctx(
    stub: Optional[OpenClawScheduleMatchStub],
    default_country: Optional[str],
) -> Optional[ScheduleMatchContextV2]:
    if not stub:
        return None
    code = stub.competition_code or "UNK"
    comp = CompetitionRefV2(competition_code=code, name=stub.competition_name or code, country=default_country)
    md = stub.match_date or date.today()
    return ScheduleMatchContextV2(
        competition=comp,
        match_date=md,
        importance=_parse_match_importance(stub.importance),
        opponent_name=stub.opponent_name or "Opponent",
        opponent_strength_band=OpponentStrengthBand.MEDIUM,
        is_home=stub.is_home,
    )


def _schedule_ctx(team: TeamRefV2, blk: Optional[OpenClawScheduleBlock], meta_country: Optional[str]) -> ScheduleContextV2:
    if not blk:
        return ScheduleContextV2(team=team)
    return ScheduleContextV2(
        team=team,
        days_since_last_match=blk.days_since_last_match,
        days_to_next_match=blk.days_to_next_match,
        matches_last_14_days=blk.matches_last_14_days or 0,
        matches_next_7_days=blk.matches_next_7_days or 0,
        prev_match=_schedule_stub_to_ctx(blk.prev_match, meta_country),
        next_match=_schedule_stub_to_ctx(blk.next_match, meta_country),
        fixture_window_difficulty_score=_clip(blk.fixture_window_difficulty_score, 0.0, 1.0, 0.0),
        travel_load_score=_clip(blk.travel_load_score, 0.0, 1.0, 0.0),
        fixture_congestion_score=_clip(blk.fixture_congestion_score, 0.0, 1.0, 0.0),
        rotation_risk_score=_clip(blk.rotation_risk_score, 0.0, 1.0, 0.0),
        pre_big_match_preservation_risk=_clip(blk.pre_big_match_preservation_risk, 0.0, 1.0, 0.0),
        post_big_match_relaxation_risk=_clip(blk.post_big_match_relaxation_risk, 0.0, 1.0, 0.0),
        emotional_swing_score=_clip(blk.emotional_swing_score, 0.0, 1.0, 0.0),
    )


def _news_ctx(nb: Optional[OpenClawNewsBlock]) -> NewsContextV2:
    if not nb:
        return NewsContextV2()
    items: List[NewsItemV2] = []
    for it in nb.items:
        items.append(
            NewsItemV2(
                title=it.title or "—",
                summary=it.summary,
                severity=it.severity or NewsSeverity.MEDIUM,
                source=it.source,
                published_at=it.published_at,
                relevance_score=_clip(it.relevance_score, 0.0, 1.0, 0.5),
            )
        )
    return NewsContextV2(
        major_news_items=items,
        locker_room_issues=list(nb.locker_room_issues),
        important_returnees=list(nb.important_returnees),
        priority_signals=list(nb.priority_signals),
        fatigue_signals=list(nb.fatigue_signals),
        rotation_signals=list(nb.rotation_signals),
        news_risk_score=_clip(nb.news_risk_score, 0.0, 1.0, 0.0) if nb.news_risk_score is not None else 0.0,
    )


def _h2h(h: Optional[OpenClawH2HBlock]) -> H2HContextV2:
    if not h:
        return H2HContextV2()
    return H2HContextV2(
        team_h2h_total_matches=int(h.team_h2h_total_matches or 0),
        team_h2h_recent_score=_clip(h.team_h2h_recent_score, -1.0, 1.0, 0.0),
        team_h2h_home_away_split=_clip(h.team_h2h_home_away_split, -1.0, 1.0, 0.0),
        h2h_btts_rate=_clip(h.h2h_btts_rate, 0.0, 1.0, 0.5),
        h2h_over25_rate=_clip(h.h2h_over25_rate, 0.0, 1.0, 0.5),
        h2h_context_bias=_clip(h.h2h_context_bias, -0.3, 0.3, 0.0),
    )


def _confidence_from_payload(payload: OpenClawMatchPayload) -> ConfidenceBreakdownV2:
    src = payload.source
    freshness = src.data_freshness_score if src else None
    completeness = src.completeness_score if src else None
    trust = src.confidence_score if src else None

    def fill_score(present: Callable[[], bool]) -> float:
        return 0.55 if present() else 0.35

    meta_ok = payload.meta is not None and payload.meta.match_date_utc is not None
    teams_ok = (
        payload.home_team is not None
        or payload.home_context is not None
        or payload.away_team is not None
        or payload.away_context is not None
    )

    coach_scores: List[float] = []
    if payload.home_coach and payload.home_coach.source_quality_score is not None:
        coach_scores.append(float(payload.home_coach.source_quality_score))
    if payload.away_coach and payload.away_coach.source_quality_score is not None:
        coach_scores.append(float(payload.away_coach.source_quality_score))

    bd = ConfidenceBreakdownV2(
        match_meta_confidence=_clip(freshness, 0.0, 1.0, 0.55) if meta_ok else 0.35,
        teams_confidence=0.5 if teams_ok else 0.35,
        squads_confidence=fill_score(lambda: bool(payload.home_squad or payload.away_squad)),
        coaches_confidence=_clip(sum(coach_scores) / len(coach_scores), 0.0, 1.0, 0.5)
        if coach_scores
        else fill_score(lambda: bool(payload.home_coach or payload.away_coach)),
        odds_confidence=0.5,
        news_confidence=fill_score(lambda: bool(payload.news and payload.news.items)),
        schedule_confidence=fill_score(lambda: bool(payload.home_schedule or payload.away_schedule)),
        h2h_confidence=fill_score(lambda: bool(payload.h2h)),
        data_freshness_score=_clip(freshness, 0.0, 1.0, 0.5) if freshness is not None else 0.5,
        source_agreement_score=_clip(trust, 0.0, 1.0, 0.5) if trust is not None else 0.5,
        overall_completeness_score=_clip(completeness, 0.0, 1.0, 0.45)
        if completeness is not None
        else 0.45,
        overall_confidence_score=0.5,
    )
    if payload.odds:
        bd.odds_confidence = _build_odds_ctx(payload.odds).odds_confidence
    oc = (bd.match_meta_confidence + bd.teams_confidence + bd.odds_confidence) / 3.0
    bd.overall_confidence_score = _clip(oc, 0.0, 1.0, 0.5)
    return bd


class OpenClawSnapshotBuilder:
    """
    Stateless adapter: OpenClawMatchPayload → MatchAnalysisSnapshotV2.

    Use when OpenClaw already produced a structured bundle (HTTP file, queue, etc.).
    """

    def build(self, payload: OpenClawMatchPayload) -> MatchAnalysisSnapshotV2:
        meta = payload.meta
        now = datetime.now(timezone.utc)

        home_ref = payload.home_team or (
            payload.home_context.team if payload.home_context else OpenClawTeamRef(name="Home")
        )
        away_ref = payload.away_team or (
            payload.away_context.team if payload.away_context else OpenClawTeamRef(name="Away")
        )

        match_date = meta.match_date_utc if meta and meta.match_date_utc else now
        season = meta.season if meta and meta.season is not None else match_date.year

        comp_name = (meta.competition_name if meta else None) or "Unknown competition"
        comp_code = (meta.competition_code if meta else None) or "UNK"
        ttype = meta.tournament_type if meta and meta.tournament_type else TournamentType.LEAGUE_REGULAR
        country = meta.country if meta else None

        seed = f"{comp_code}|{home_ref.name}|{away_ref.name}|{match_date.isoformat()}"
        match_id = (
            int(meta.internal_match_id_hint)
            if meta and meta.internal_match_id_hint is not None
            else _stable_match_id(seed)
        )

        home_tr = _team_ref(home_ref)
        away_tr = _team_ref(away_ref)

        match_meta = MatchMetaV2(
            match_id=match_id,
            season=int(season),
            competition_name=comp_name,
            competition_code=comp_code,
            tournament_type=ttype,
            season_phase=_season_phase_from_round(meta.round_number if meta else None) if meta else None,
            stage=meta.stage if meta else None,
            round_number=meta.round_number if meta else None,
            match_date_utc=match_date if match_date.tzinfo else match_date.replace(tzinfo=timezone.utc),
            country=country,
            venue_name=meta.venue.name if meta and meta.venue else None,
            is_neutral_venue=bool(meta.venue.is_neutral) if meta and meta.venue else False,
            home_team=home_tr,
            away_team=away_tr,
            season_progress=0.5,
        )

        home_team_ctx = _team_ctx(home_ref, payload.home_context)
        away_team_ctx = _team_ctx(away_ref, payload.away_context)

        home_squad = _squad(home_tr, payload.home_squad)
        away_squad = _squad(away_tr, payload.away_squad)

        home_coach = _coach(home_tr, payload.home_coach)
        away_coach = _coach(away_tr, payload.away_coach)

        home_sched = _schedule_ctx(home_tr, payload.home_schedule, country)
        away_sched = _schedule_ctx(away_tr, payload.away_schedule, country)

        odds = _build_odds_ctx(payload.odds)
        news = _news_ctx(payload.news)
        h2h = _h2h(payload.h2h)

        conf = _confidence_from_payload(payload)
        conf.odds_confidence = odds.odds_confidence

        tags: List[str] = ["openclaw"]
        if payload.source and payload.source.tags:
            tags.extend(payload.source.tags)
        if payload.source and payload.source.source_name:
            tags.append(payload.source.source_name)

        collected = payload.source.collected_at if payload.source and payload.source.collected_at else now

        return MatchAnalysisSnapshotV2(
            match_meta=match_meta,
            home_team_context=home_team_ctx,
            away_team_context=away_team_ctx,
            home_squad=home_squad,
            away_squad=away_squad,
            home_coach=home_coach,
            away_coach=away_coach,
            home_schedule=home_sched,
            away_schedule=away_sched,
            odds=odds,
            news_context=news,
            h2h_context=h2h,
            confidence=conf,
            collected_at=collected,
            updated_at=now,
            source_tags=tags,
            snapshot_version="2.0.0+openclaw",
        )
