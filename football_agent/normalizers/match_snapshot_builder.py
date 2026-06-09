"""
Build MatchAnalysisSnapshotV2 from v1 API clients and legacy domain models.

v1 APIs / Match + StandingEntry + MatchResult → normalizer → MatchAnalysisSnapshotV2

Does not run scorer, market probabilities, or express selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from football_agent.data_providers.odds_utils import count_odds_fields, infer_api_football_season, seasons_to_try
from football_agent.config import CURRENT_SEASON
from football_agent.league_registry import LeagueParams, resolve_league_params
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.enums_v2 import (
    CoachTenurePhase,
    MathematicalGoalStatus,
    MotivationContext,
    SeasonPhase,
    TournamentType,
)
from football_agent.domain.features import (
    calculate_coach_strength,
    calculate_form,
    calculate_h2h_stats,
    calculate_motivation,
)
from football_agent.domain.models import Match, MatchResult, Odds, StandingEntry, Team
from football_agent.domain.models_v2 import (
    CoachContextV2,
    CoachRefV2,
    CompetitionRefV2,
    ConfidenceBreakdownV2,
    H2HContextV2,
    MatchAnalysisSnapshotV2,
    MatchMetaV2,
    NewsContextV2,
    OddsContextV2,
    OddsMarketV2,
    ScheduleContextV2,
    ScheduleMatchContextV2,
    SquadContextV2,
    TeamContextV2,
    TeamFormBlockV2,
    TeamMotivationBlockV2,
    TeamRefV2,
    TeamScheduleMiniBlockV2,
)
from football_agent.domain.probability_model import compute_h2h_bias, compute_season_progress

logger = logging.getLogger(__name__)

def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pts_rate(matches: List[MatchResult]) -> float:
    if not matches:
        return 0.5
    pts = sum(3 if m.result == "W" else 1 if m.result == "D" else 0 for m in matches)
    return pts / (3 * len(matches))


@dataclass
class _LeagueContext:
    standings: List[StandingEntry]
    played_rounds: int
    total_rounds: Optional[int]
    season_progress: Optional[float]
    league_params: LeagueParams


@dataclass
class _BlockConfidence:
    """Per-block completeness flags for confidence heuristic."""

    match_meta: float = 0.9
    teams: float = 0.0
    squads: float = 0.15
    coaches: float = 0.0
    odds: float = 0.0
    news: float = 0.1
    schedule: float = 0.0
    h2h: float = 0.0
    source_tags: List[str] = field(default_factory=list)


class MatchSnapshotBuilder:
    """Fail-soft builder: v1 clients → MatchAnalysisSnapshotV2."""

    def __init__(
        self,
        football_data_client: FootballDataClient,
        api_football_client: ApiFootballClient,
        season: int = CURRENT_SEASON,
    ):
        self._fd = football_data_client
        self._af = api_football_client
        self._season = season
        self._standings_cache: Dict[str, _LeagueContext] = {}

    def build_snapshot_for_match(self, match: Match) -> MatchAnalysisSnapshotV2:
        code = match.competition_code
        conf = _BlockConfidence(source_tags=["football-data.org"])

        league_ctx = self._get_league_context(code)
        home_id, away_id = match.home_team.id, match.away_team.id

        home_entry = self._standing_entry(league_ctx, home_id) if league_ctx else None
        away_entry = self._standing_entry(league_ctx, away_id) if league_ctx else None
        if home_entry and away_entry:
            conf.teams = 0.85
        else:
            conf.teams = 0.35
            logger.warning("Standings incomplete for match %s (%s)", match.id, code)

        home_matches = self._safe_season_matches(home_id)
        away_matches = self._safe_season_matches(away_id)

        home_coach_raw = self._safe_coach_bundle(home_id)
        away_coach_raw = self._safe_coach_bundle(away_id)

        h2h_matches = self._safe_h2h(home_id, away_id)
        h2h_v1 = calculate_h2h_stats(h2h_matches, home_id)
        conf.h2h = _clip01(h2h_v1.total_matches / 8.0) * 0.85 if h2h_v1.total_matches else 0.2

        odds_v1 = self._safe_odds(match)
        if odds_v1 and any(
            getattr(odds_v1, k) is not None
            for k in (
                "home_win",
                "draw",
                "away_win",
                "home_not_lose",
                "away_not_lose",
                "btts_yes",
                "home_team_to_score",
                "away_team_to_score",
                "over_15",
            )
        ):
            conf.odds = 0.8
            conf.source_tags.append("api-football")
        else:
            conf.odds = 0.15

        if home_matches or away_matches:
            conf.schedule = 0.55
        if home_coach_raw[0] or away_coach_raw[0]:
            conf.coaches = 0.7

        match_meta = self._build_match_meta(match, league_ctx)
        home_team_ctx = self._build_team_context(
            match.home_team,
            competition_code=code,
            is_home=True,
            entry=home_entry,
            league_ctx=league_ctx,
            season_matches=home_matches,
            coach_start=home_coach_raw[2],
            schedule_matches=home_matches,
            match_date=match.utc_date,
        )
        away_team_ctx = self._build_team_context(
            match.away_team,
            competition_code=code,
            is_home=False,
            entry=away_entry,
            league_ctx=league_ctx,
            season_matches=away_matches,
            coach_start=away_coach_raw[2],
            schedule_matches=away_matches,
            match_date=match.utc_date,
        )

        home_squad = self._build_empty_squad(match.home_team)
        away_squad = self._build_empty_squad(match.away_team)

        home_coach = self._build_coach_context(
            match.home_team,
            away_id,
            home_coach_raw,
            away_coach_raw[4] if away_coach_raw else [],
        )
        away_coach = self._build_coach_context(
            match.away_team,
            home_id,
            away_coach_raw,
            home_coach_raw[4] if home_coach_raw else [],
        )

        home_schedule = self._build_schedule_context(
            match.home_team,
            competition_code=code,
            season_matches=home_matches,
            match_date=match.utc_date,
            league_ctx=league_ctx,
        )
        away_schedule = self._build_schedule_context(
            match.away_team,
            competition_code=code,
            season_matches=away_matches,
            match_date=match.utc_date,
            league_ctx=league_ctx,
        )

        odds_ctx = self._build_odds_context(odds_v1)
        news_ctx = NewsContextV2()
        h2h_ctx = self._build_h2h_context(h2h_v1)
        confidence = self._build_confidence(conf)

        return MatchAnalysisSnapshotV2(
            match_meta=match_meta,
            home_team_context=home_team_ctx,
            away_team_context=away_team_ctx,
            home_squad=home_squad,
            away_squad=away_squad,
            home_coach=home_coach,
            away_coach=away_coach,
            home_schedule=home_schedule,
            away_schedule=away_schedule,
            odds=odds_ctx,
            news_context=news_ctx,
            h2h_context=h2h_ctx,
            confidence=confidence,
            source_tags=sorted(set(conf.source_tags)),
        )

    def build_snapshots_for_date(self, date_str: str) -> List[MatchAnalysisSnapshotV2]:
        snapshots: List[MatchAnalysisSnapshotV2] = []
        for match in self._fd.get_matches_by_date(date_str):
            try:
                snapshots.append(self.build_snapshot_for_match(match))
            except Exception as e:
                logger.warning(
                    "Failed snapshot for match %s (%s vs %s): %s",
                    match.id,
                    match.home_team.name,
                    match.away_team.name,
                    e,
                )
        return snapshots

    # ------------------------------------------------------------------
    # League / standings
    # ------------------------------------------------------------------

    def _get_league_context(self, competition_code: str) -> Optional[_LeagueContext]:
        if competition_code in self._standings_cache:
            return self._standings_cache[competition_code]

        standings = self._fd.get_standings(competition_code)
        if not standings:
            return None

        params = resolve_league_params(competition_code)
        total_rounds = params.total_rounds
        played = max((s.played_games for s in standings), default=0)
        if total_rounds is None:
            season_progress = None
            logger.warning(
                "Season progress unavailable for %s: unknown_total_rounds_for_competition",
                competition_code,
            )
        else:
            season_progress = compute_season_progress(played, total_rounds)
        ctx = _LeagueContext(
            standings=standings,
            played_rounds=played,
            total_rounds=total_rounds,
            season_progress=season_progress,
            league_params=params,
        )
        self._standings_cache[competition_code] = ctx
        return ctx

    @staticmethod
    def _standing_entry(ctx: _LeagueContext, team_id: int) -> Optional[StandingEntry]:
        return next((s for s in ctx.standings if s.team.id == team_id), None)

    # ------------------------------------------------------------------
    # Safe API wrappers
    # ------------------------------------------------------------------

    def _safe_season_matches(self, team_id: int) -> List[MatchResult]:
        try:
            return self._fd.get_team_matches_season(team_id, self._season)
        except Exception as e:
            logger.warning("Season matches failed for team %s: %s", team_id, e)
            return []

    def _safe_coach_bundle(
        self, team_id: int
    ) -> Tuple[Optional[int], Optional[str], Optional[date], List[MatchResult], List]:
        """Returns (coach_id, coach_name, coach_start_date, coach_matches, placeholder)."""
        try:
            coach_id, coach_name, coach_start = self._fd.get_team_coach(team_id)
            coach_matches = self._fd.get_coach_matches(coach_id) if coach_id else []
            return coach_id, coach_name, coach_start, coach_matches, coach_matches
        except Exception as e:
            logger.warning("Coach fetch failed for team %s: %s", team_id, e)
            return None, None, None, [], []

    def _safe_h2h(self, home_id: int, away_id: int) -> List[MatchResult]:
        try:
            return self._fd.get_h2h_matches(home_id, away_id, self._season)
        except Exception as e:
            logger.warning("H2H failed %s vs %s: %s", home_id, away_id, e)
            return []

    def _safe_odds(self, match: Match) -> Optional[Odds]:
        league_id = resolve_league_params(match.competition_code).api_football_league_id
        if league_id is None:
            return None
        date_str = match.utc_date.strftime("%Y-%m-%d")
        api_season = infer_api_football_season(match.utc_date)
        try_seasons = seasons_to_try(api_season, self._season)
        try:
            fixture_id = self._af.find_fixture_id(
                home_name=match.home_team.name,
                away_name=match.away_team.name,
                date_str=date_str,
                league_id=league_id,
                season=self._season,
                seasons=try_seasons,
            )
            if fixture_id is None:
                logger.info(
                    "Odds: no fixture %s vs %s on %s (seasons tried %s)",
                    match.home_team.name,
                    match.away_team.name,
                    date_str,
                    try_seasons,
                )
                return None
            odds = self._af.get_odds(fixture_id)
            if odds is not None and count_odds_fields(odds) == 0:
                logger.warning("Odds: fixture %s found but no markets parsed", fixture_id)
            return odds
        except Exception as e:
            logger.warning("Odds fetch failed for match %s: %s", match.id, e)
            return None

    # ------------------------------------------------------------------
    # v1 → v2 mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _team_ref(team: Team) -> TeamRefV2:
        return TeamRefV2(
            team_id=team.id,
            name=team.name,
            short_name=team.short_name or team.name,
        )

    @staticmethod
    def _competition_ref(code: str) -> CompetitionRefV2:
        params = resolve_league_params(code)
        return CompetitionRefV2(
            competition_code=params.competition_code,
            name=params.display_name,
            country=params.country,
            tournament_type=TournamentType.LEAGUE_REGULAR,
        )

    # ------------------------------------------------------------------
    # Match meta
    # ------------------------------------------------------------------

    def _build_match_meta(self, match: Match, league_ctx: Optional[_LeagueContext]) -> MatchMetaV2:
        code = match.competition_code
        params = league_ctx.league_params if league_ctx else resolve_league_params(code)
        sp = league_ctx.season_progress if league_ctx else None
        played = league_ctx.played_rounds if league_ctx else None
        total = league_ctx.total_rounds if league_ctx else params.total_rounds
        remaining = (total - played) if total is not None and played is not None else None
        if sp is None:
            season_phase = SeasonPhase.UNKNOWN
            sp_value = 0.0
        else:
            season_phase = self._season_phase(sp)
            sp_value = sp

        return MatchMetaV2(
            match_id=match.id,
            season=self._season,
            competition_name=params.display_name,
            competition_code=params.competition_code,
            tournament_type=TournamentType.LEAGUE_REGULAR,
            season_phase=season_phase,
            stage=None,
            round_number=match.matchday if match.matchday else None,
            match_date_utc=match.utc_date,
            country=params.country,
            venue_name=None,
            is_neutral_venue=False,
            home_team=self._team_ref(match.home_team),
            away_team=self._team_ref(match.away_team),
            season_progress=sp_value,
            rounds_played=played,
            rounds_remaining=remaining,
        )

    @staticmethod
    def _season_phase(season_progress: float) -> SeasonPhase:
        if season_progress < 0.25:
            return SeasonPhase.EARLY
        if season_progress < 0.65:
            return SeasonPhase.MID
        if season_progress < 0.85:
            return SeasonPhase.LATE
        return SeasonPhase.FINAL_RUN_IN

    # ------------------------------------------------------------------
    # Team context
    # ------------------------------------------------------------------

    def _build_team_context(
        self,
        team: Team,
        competition_code: str,
        is_home: bool,
        entry: Optional[StandingEntry],
        league_ctx: Optional[_LeagueContext],
        season_matches: List[MatchResult],
        coach_start: Optional[date],
        schedule_matches: List[MatchResult],
        match_date: datetime,
    ) -> TeamContextV2:
        team_ref = self._team_ref(team)
        form_block = self._build_form_block(season_matches, coach_start, is_home)
        motivation_block = self._build_motivation_block(competition_code, entry, league_ctx)
        schedule_mini = self._build_schedule_mini(schedule_matches, match_date)

        baseline = 0.5
        if entry and league_ctx:
            total_teams = len(league_ctx.standings)
            if total_teams > 1:
                baseline = 1.0 - (entry.position - 1) / (total_teams - 1)

        return TeamContextV2(
            team=team_ref,
            baseline_strength_score=_clip01(baseline),
            form=form_block,
            motivation=motivation_block,
            schedule=schedule_mini,
            availability_score=0.5,
            bench_quality_score=0.5,
            line_stability_score=0.5,
        )

    def _build_form_block(
        self,
        season_matches: List[MatchResult],
        coach_start: Optional[date],
        is_home: bool,
    ) -> TeamFormBlockV2:
        finished = [m for m in season_matches if m.result in ("W", "D", "L")]
        if not finished:
            combined = calculate_form([], coach_start, is_home)
            return TeamFormBlockV2(form_under_current_coach=combined)

        recent5 = sorted(finished, key=lambda m: m.date, reverse=True)[:5]
        recent10 = sorted(finished, key=lambda m: m.date, reverse=True)[:10]
        home_m = [m for m in finished if m.is_home]
        away_m = [m for m in finished if not m.is_home]

        if coach_start:
            coach_m = [m for m in finished if m.date >= coach_start]
        else:
            coach_m = finished

        last5 = _pts_rate(recent5)
        last10 = _pts_rate(recent10)
        trend = last5 - last10 if len(recent10) >= 3 else 0.0

        return TeamFormBlockV2(
            last_5_form_score=_clip01(last5),
            last_10_form_score=_clip01(last10),
            home_form_score=_clip01(_pts_rate(home_m)),
            away_form_score=_clip01(_pts_rate(away_m)),
            form_under_current_coach=_clip01(_pts_rate(coach_m) if coach_m else last10),
            performance_trend_score=max(-1.0, min(1.0, trend * 2.0)),
        )

    def _build_motivation_block(
        self,
        competition_code: str,
        entry: Optional[StandingEntry],
        league_ctx: Optional[_LeagueContext],
    ) -> TeamMotivationBlockV2:
        if not entry or not league_ctx:
            return TeamMotivationBlockV2()

        m_score, eliminated, _, mot_warnings = calculate_motivation(
            position=entry.position,
            points=entry.points,
            played_rounds=league_ctx.played_rounds,
            total_rounds=league_ctx.total_rounds,
            standings=league_ctx.standings,
            competition_code=competition_code,
        )

        return TeamMotivationBlockV2(
            derivation_warnings=list(mot_warnings),
            motivation_context=self._infer_motivation_context(
                competition_code, entry, league_ctx, eliminated
            ),
            motivation_score=_clip01(m_score),
            mathematical_goal_status=self._math_goal_status(entry, league_ctx, eliminated),
            league_position=entry.position,
            points=entry.points,
            goal_difference=entry.goal_difference,
            points_gap_to_target_up=self._points_gap_up(entry, league_ctx),
            points_gap_to_target_down=self._points_gap_down(competition_code, entry, league_ctx),
        )

    def _infer_motivation_context(
        self,
        competition_code: str,
        entry: StandingEntry,
        league_ctx: _LeagueContext,
        eliminated: bool,
    ) -> MotivationContext:
        params = resolve_league_params(competition_code)
        total = len(league_ctx.standings)
        rel_border = total - params.relegation_slots + 1
        euro_border = params.euro_slots["ucl"] + params.euro_slots["uel"]

        if eliminated or entry.position >= rel_border:
            return MotivationContext.RELEGATION_BATTLE
        if entry.position == 1:
            return MotivationContext.TITLE_RACE
        if entry.position <= euro_border:
            return MotivationContext.EURO_RACE
        if 5 < entry.position < total - 4:
            return MotivationContext.MIDTABLE_NEUTRAL
        return MotivationContext.SAFE_NO_TARGET

    @staticmethod
    def _math_goal_status(
        entry: StandingEntry,
        league_ctx: _LeagueContext,
        eliminated: bool,
    ) -> MathematicalGoalStatus:
        if eliminated:
            return MathematicalGoalStatus.ELIMINATED
        if entry.position == 1 and entry.points >= league_ctx.played_rounds * 2:
            return MathematicalGoalStatus.SECURED
        return MathematicalGoalStatus.ACHIEVABLE

    @staticmethod
    def _points_gap_up(entry: StandingEntry, league_ctx: _LeagueContext) -> Optional[int]:
        if entry.position <= 1:
            return 0
        above = next((s for s in league_ctx.standings if s.position == entry.position - 1), None)
        return (above.points - entry.points) if above else None

    @staticmethod
    def _points_gap_down(
        competition_code: str, entry: StandingEntry, league_ctx: _LeagueContext
    ) -> Optional[int]:
        params = resolve_league_params(competition_code)
        total = len(league_ctx.standings)
        rel_border = total - params.relegation_slots + 1
        below = next((s for s in league_ctx.standings if s.position == rel_border), None)
        if not below:
            return None
        return entry.points - below.points

    def _build_schedule_mini(
        self,
        season_matches: List[MatchResult],
        match_date: datetime,
    ) -> TeamScheduleMiniBlockV2:
        finished = sorted(
            [m for m in season_matches if m.result in ("W", "D", "L")],
            key=lambda m: m.date,
        )
        if not finished:
            return TeamScheduleMiniBlockV2()

        ref = match_date.date() if isinstance(match_date, datetime) else match_date
        last14 = [m for m in finished if (ref - m.date).days <= 14]
        congestion = _clip01(len(last14) / 5.0)

        return TeamScheduleMiniBlockV2(
            fixture_congestion_score=congestion,
            rotation_risk_score=_clip01(congestion * 0.6),
            pre_big_match_preservation_risk=0.0,
            post_big_match_relaxation_risk=0.0,
        )

    # ------------------------------------------------------------------
    # Squad (empty baseline)
    # ------------------------------------------------------------------

    def _build_empty_squad(self, team: Team) -> SquadContextV2:
        return SquadContextV2(
            team=self._team_ref(team),
            starting_xi_confidence=0.2,
            line_stability_score=0.5,
        )

    # ------------------------------------------------------------------
    # Coach
    # ------------------------------------------------------------------

    def _build_coach_context(
        self,
        team: Team,
        opponent_id: int,
        coach_bundle: Tuple[Optional[int], Optional[str], Optional[date], list, list],
        opponent_coach_matches: list,
    ) -> CoachContextV2:
        coach_id, coach_name, coach_start, coach_matches, _ = coach_bundle
        team_ref = self._team_ref(team)

        if not coach_id or not coach_name:
            return CoachContextV2(
                coach=CoachRefV2(coach_id=None, name="Unknown"),
                team=team_ref,
            )

        matches_in_charge = len([m for m in coach_matches if m.team_id == team.id]) if coach_matches else 0

        days_in_charge = None
        if coach_start:
            days_in_charge = (date.today() - coach_start).days

        is_first = matches_in_charge <= 1
        is_bounce = 2 <= matches_in_charge <= 4
        tenure = CoachTenurePhase.ESTABLISHED
        if is_first:
            tenure = CoachTenurePhase.FIRST_MATCH
        elif is_bounce:
            tenure = CoachTenurePhase.BOUNCE_WINDOW

        raw_strength = calculate_coach_strength(coach_matches, opponent_id)
        raw_vs_coach = 0.5
        if opponent_coach_matches:
            raw_vs_coach = calculate_coach_strength(opponent_coach_matches, team.id)

        return CoachContextV2(
            coach=CoachRefV2(coach_id=coach_id, name=coach_name),
            team=team_ref,
            coach_start_date=coach_start,
            days_in_charge=days_in_charge,
            matches_in_charge=matches_in_charge or None,
            tenure_phase=tenure,
            is_first_match=is_first,
            is_new_coach_bounce_window=is_bounce,
            coach_global_strength_score=_clip01(raw_strength),
            coach_vs_opponent_team_score=_clip01(raw_strength),
            coach_vs_opponent_coach_score=_clip01(raw_vs_coach),
            coach_rotation_tendency_score=0.5,
        )

    # ------------------------------------------------------------------
    # Schedule (simplified)
    # ------------------------------------------------------------------

    def _build_schedule_context(
        self,
        team: Team,
        competition_code: str,
        season_matches: List[MatchResult],
        match_date: datetime,
        league_ctx: Optional[_LeagueContext],
    ) -> ScheduleContextV2:
        team_ref = self._team_ref(team)
        finished = sorted(
            [m for m in season_matches if m.result in ("W", "D", "L")],
            key=lambda m: m.date,
        )
        ref = match_date.date()

        prev_m = next((m for m in reversed(finished) if m.date < ref), None)
        next_m = next((m for m in finished if m.date > ref), None)

        days_since = (ref - prev_m.date).days if prev_m else None
        days_to = (next_m.date - ref).days if next_m else None

        last14 = sum(1 for m in finished if 0 < (ref - m.date).days <= 14)
        next7 = sum(1 for m in finished if 0 < (m.date - ref).days <= 7)

        congestion = _clip01(last14 / 5.0)

        return ScheduleContextV2(
            team=team_ref,
            days_since_last_match=days_since,
            days_to_next_match=days_to,
            matches_last_14_days=last14,
            matches_next_7_days=next7,
            prev_match=self._schedule_match_stub(prev_m, competition_code) if prev_m else None,
            next_match=self._schedule_match_stub(next_m, competition_code) if next_m else None,
            fixture_window_difficulty_score=congestion,
            travel_load_score=0.0,
            fixture_congestion_score=congestion,
            rotation_risk_score=_clip01(congestion * 0.5),
            pre_big_match_preservation_risk=0.0,
            post_big_match_relaxation_risk=0.0,
            emotional_swing_score=0.0,
        )

    def _schedule_match_stub(self, m: MatchResult, competition_code: str) -> ScheduleMatchContextV2:
        comp = self._competition_ref(competition_code)
        return ScheduleMatchContextV2(
            competition=comp,
            match_date=m.date,
            opponent_name="Opponent",
            is_home=m.is_home,
        )

    # ------------------------------------------------------------------
    # Odds / H2H / confidence
    # ------------------------------------------------------------------

    @staticmethod
    def _build_odds_context(odds: Optional[Odds]) -> OddsContextV2:
        if not odds:
            return OddsContextV2(odds_confidence=0.15)

        def market(key: str, name: str, selection: str, value: Optional[float]) -> Optional[OddsMarketV2]:
            if value is None:
                return None
            return OddsMarketV2(
                market_key=key,
                market_name=name,
                selection_name=selection,
                odds=float(value),
                source="api-football",
            )

        values = (
            odds.home_win,
            odds.draw,
            odds.away_win,
            odds.home_not_lose,
            odds.away_not_lose,
            odds.btts_yes,
            odds.home_team_to_score,
            odds.away_team_to_score,
            odds.over_15,
        )
        filled = sum(1 for v in values if v is not None)
        return OddsContextV2(
            home_win=market("HOME_WIN", "Match Winner", "Home", odds.home_win),
            draw=market("DRAW", "Match Winner", "Draw", odds.draw),
            away_win=market("AWAY_WIN", "Match Winner", "Away", odds.away_win),
            home_not_lose=market("HOME_NOT_LOSE", "Double Chance", "Home/Draw", odds.home_not_lose),
            away_not_lose=market("AWAY_NOT_LOSE", "Double Chance", "Draw/Away", odds.away_not_lose),
            btts_yes=market("BTTS_YES", "Both Teams Score", "Yes", odds.btts_yes),
            home_team_to_score=market(
                "HOME_TEAM_TO_SCORE", "Home Team To Score", "Yes", odds.home_team_to_score
            ),
            away_team_to_score=market(
                "AWAY_TEAM_TO_SCORE", "Away Team To Score", "Yes", odds.away_team_to_score
            ),
            over_15=market("OVER_1_5", "Goals Over/Under", "Over 1.5", odds.over_15),
            odds_confidence=_clip01(0.3 + filled / 9.0 * 0.7),
        )

    @staticmethod
    def _build_h2h_context(h2h_v1) -> H2HContextV2:
        bias = compute_h2h_bias(h2h_v1)
        total = h2h_v1.total_matches
        recent_score = 0.0
        if total > 0 and (h2h_v1.home_wins + h2h_v1.away_wins) > 0:
            recent_score = (h2h_v1.home_wins - h2h_v1.away_wins) / max(h2h_v1.home_wins + h2h_v1.away_wins, 1)
            recent_score = max(-1.0, min(1.0, recent_score))

        return H2HContextV2(
            team_h2h_total_matches=total,
            team_h2h_recent_score=recent_score,
            team_h2h_home_away_split=recent_score * 0.5,
            h2h_btts_rate=float(h2h_v1.btts_rate),
            h2h_over25_rate=float(h2h_v1.over25_rate),
            h2h_context_bias=bias,
        )

    @staticmethod
    def _build_confidence(conf: _BlockConfidence) -> ConfidenceBreakdownV2:
        block_scores = [
            conf.match_meta,
            conf.teams,
            conf.squads,
            conf.coaches,
            conf.odds,
            conf.news,
            conf.schedule,
            conf.h2h,
        ]
        completeness = sum(block_scores) / len(block_scores)
        overall = completeness * 0.85

        return ConfidenceBreakdownV2(
            match_meta_confidence=conf.match_meta,
            teams_confidence=conf.teams,
            squads_confidence=conf.squads,
            coaches_confidence=conf.coaches,
            odds_confidence=conf.odds,
            news_confidence=conf.news,
            schedule_confidence=conf.schedule,
            h2h_confidence=conf.h2h,
            data_freshness_score=0.65,
            source_agreement_score=0.6 if len(conf.source_tags) > 1 else 0.45,
            overall_completeness_score=_clip01(completeness),
            overall_confidence_score=_clip01(overall),
        )


if __name__ == "__main__":
    import json
    import os

    from football_agent.config import API_FOOTBALL_KEY, FOOTBALL_DATA_API_KEY

    logging.basicConfig(level=logging.INFO)

    if not FOOTBALL_DATA_API_KEY:
        print("Set FOOTBALL_DATA_API_KEY in .env to run live example.")
        raise SystemExit(0)

    fd = FootballDataClient(FOOTBALL_DATA_API_KEY)
    af = ApiFootballClient(API_FOOTBALL_KEY or "")
    builder = MatchSnapshotBuilder(fd, af)

    date_str = "2024-04-25"
    matches = fd.get_matches_by_date(date_str)
    if not matches:
        print(f"No matches on {date_str}")
        raise SystemExit(0)

    snapshot = builder.build_snapshot_for_match(matches[0])
    print(json.dumps(snapshot.model_dump(mode="json"), indent=2, default=str)[:2000])
