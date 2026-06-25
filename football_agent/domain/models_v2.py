"""
Pydantic v2 domain contracts for RobinHoodForecast match analysis (v2).

Lives alongside v1 models in `models.py` — do not replace v1 imports.
See docs/league_logic_blueprint.md for semantic definitions.

Score conventions (enforced where noted):
- Unit interval [0, 1]: strengths, probabilities, risks, confidence.
- Signed [-1, 1]: trends and directional H2H splits.
- Signed [-0.3, 0.3]: weak 1X2 H2H bias (blueprint).
- `edge`: unbounded EV margin; may be negative.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from football_agent.domain.enums_v2 import (
    AvailabilityStatus,
    CoachTenurePhase,
    ExpressSafetyClass,
    MatchImportance,
    MathematicalGoalStatus,
    MotivationContext,
    NewsSeverity,
    OpponentStrengthBand,
    PlayerImportance,
    SeasonPhase,
    TournamentType,
)


def _utc_now() -> datetime:
    """Timezone-aware UTC timestamp for snapshot metadata."""
    return datetime.now(timezone.utc)


class V2IngestModel(BaseModel):
    """
    Ingestion-friendly contracts (collectors, normalizers, snapshot).

    `extra='ignore'` drops unknown keys from external APIs/OpenClaw instead of
    failing the whole payload. Downstream code must not rely on silent fields.
    """

    model_config = ConfigDict(extra="ignore")


class V2OutputModel(BaseModel):
    """
    Scorer/service outputs: reject unknown fields to catch typos early.
    """

    model_config = ConfigDict(extra="forbid")


# Backward-compatible alias (if referenced elsewhere)
V2BaseModel = V2IngestModel


# ---------------------------------------------------------------------------
# 1. Base references
# ---------------------------------------------------------------------------


class TeamRefV2(V2IngestModel):
    team_id: int
    name: str
    short_name: Optional[str] = None
    country: Optional[str] = None


class CoachRefV2(V2IngestModel):
    coach_id: Optional[int] = None
    name: str
    nationality: Optional[str] = None


class PlayerRefV2(V2IngestModel):
    player_id: Optional[int] = None
    name: str
    position: Optional[str] = None
    shirt_number: Optional[int] = None


class CompetitionRefV2(V2IngestModel):
    competition_code: str
    name: str
    country: Optional[str] = None
    tournament_type: TournamentType = TournamentType.LEAGUE_REGULAR
    competition_family: Optional[str] = None
    competition_subtype: Optional[str] = None
    is_women: bool = False
    is_youth: bool = False
    is_reserve: bool = False


# ---------------------------------------------------------------------------
# 2. Match meta
# ---------------------------------------------------------------------------


class MatchMetaV2(V2IngestModel):
    match_id: int
    season: int
    competition_name: str
    competition_code: str
    tournament_type: TournamentType = TournamentType.LEAGUE_REGULAR
    season_phase: Optional[SeasonPhase] = None
    stage: Optional[str] = None
    round_number: Optional[int] = None
    match_date_utc: datetime
    country: Optional[str] = None
    venue_name: Optional[str] = None
    is_neutral_venue: bool = False
    home_team: TeamRefV2
    away_team: TeamRefV2
    season_progress: float = Field(ge=0.0, le=1.0, default=0.0)
    rounds_played: Optional[int] = None
    rounds_remaining: Optional[int] = None
    competition_family: Optional[str] = None
    competition_subtype: Optional[str] = None
    is_women: bool = False
    is_youth: bool = False
    is_reserve: bool = False


# ---------------------------------------------------------------------------
# 3. Team blocks (form, motivation, schedule mini, aggregated context)
# ---------------------------------------------------------------------------


class TeamFormBlockV2(V2IngestModel):
    last_5_form_score: float = Field(ge=0.0, le=1.0, default=0.5)
    last_10_form_score: float = Field(ge=0.0, le=1.0, default=0.5)
    home_form_score: float = Field(ge=0.0, le=1.0, default=0.5)
    away_form_score: float = Field(ge=0.0, le=1.0, default=0.5)
    form_under_current_coach: float = Field(ge=0.0, le=1.0, default=0.5)
    performance_trend_score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Signed form trend: +1 improving, -1 declining, 0 flat.",
    )


class TeamMotivationBlockV2(V2IngestModel):
    derivation_warnings: List[str] = Field(
        default_factory=list,
        description="Normalizer warnings when motivation/season inputs are incomplete.",
    )
    motivation_context: Optional[MotivationContext] = Field(
        default=None,
        description="None until classified by normalizer/scorer.",
    )
    motivation_score: float = Field(ge=0.0, le=1.0, default=0.5)
    mathematical_goal_status: Optional[MathematicalGoalStatus] = None
    league_position: Optional[int] = None
    points: Optional[int] = None
    goal_difference: Optional[int] = None
    points_gap_to_target_up: Optional[int] = None
    points_gap_to_target_down: Optional[int] = None


class TeamScheduleMiniBlockV2(V2IngestModel):
    fixture_congestion_score: float = Field(ge=0.0, le=1.0, default=0.0)
    rotation_risk_score: float = Field(ge=0.0, le=1.0, default=0.0)
    pre_big_match_preservation_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    post_big_match_relaxation_risk: float = Field(ge=0.0, le=1.0, default=0.0)


class TeamContextV2(V2IngestModel):
    team: TeamRefV2
    baseline_strength_score: float = Field(ge=0.0, le=1.0, default=0.5)
    form: TeamFormBlockV2 = Field(default_factory=TeamFormBlockV2)
    motivation: TeamMotivationBlockV2 = Field(default_factory=TeamMotivationBlockV2)
    schedule: TeamScheduleMiniBlockV2 = Field(default_factory=TeamScheduleMiniBlockV2)
    availability_score: float = Field(ge=0.0, le=1.0, default=0.5)
    bench_quality_score: float = Field(ge=0.0, le=1.0, default=0.5)
    line_stability_score: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# 4. Squad & availability
# ---------------------------------------------------------------------------


class PlayerAvailabilityV2(V2IngestModel):
    player: PlayerRefV2
    status: AvailabilityStatus
    importance: PlayerImportance = PlayerImportance.MEDIUM
    reason: Optional[str] = None
    expected_return_date: Optional[date] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class SquadContextV2(V2IngestModel):
    team: TeamRefV2
    expected_starting_xi: List[PlayerRefV2] = Field(default_factory=list)
    bench_players: List[PlayerRefV2] = Field(default_factory=list)
    missing_players: List[PlayerAvailabilityV2] = Field(default_factory=list)
    suspended_players: List[PlayerAvailabilityV2] = Field(default_factory=list)
    doubtful_players: List[PlayerAvailabilityV2] = Field(default_factory=list)
    missing_players_count: int = 0
    missing_key_players_count: int = 0
    starting_xi_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    line_stability_score: float = Field(ge=0.0, le=1.0, default=0.5)
    availability_score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Honest squad availability aggregate (XI confidence + absences).",
    )
    key_absence_impact_score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Estimated impact of key-role absences; 0 when unknown/no signal.",
    )


# ---------------------------------------------------------------------------
# 5. Coach
# ---------------------------------------------------------------------------


class CoachContextV2(V2IngestModel):
    coach: CoachRefV2
    team: TeamRefV2
    coach_start_date: Optional[date] = None
    days_in_charge: Optional[int] = None
    matches_in_charge: Optional[int] = None
    tenure_phase: CoachTenurePhase = CoachTenurePhase.ESTABLISHED
    # Rule-oriented flags (blueprint §18); may diverge from tenure_phase when overridden
    is_first_match: bool = False
    is_new_coach_bounce_window: bool = False
    previous_teams: List[str] = Field(default_factory=list)
    previous_national_teams: List[str] = Field(default_factory=list)
    coach_global_strength_score: float = Field(ge=0.0, le=1.0, default=0.5)
    coach_vs_opponent_team_score: float = Field(ge=0.0, le=1.0, default=0.5)
    coach_vs_opponent_coach_score: float = Field(ge=0.0, le=1.0, default=0.5)
    coach_rotation_tendency_score: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# 6. Odds
# ---------------------------------------------------------------------------


class OddsMarketV2(V2IngestModel):
    market_key: str
    market_name: str
    selection_name: str
    odds: float = Field(gt=1.0)
    bookmaker: Optional[str] = None
    source: Optional[str] = None
    collected_at: Optional[datetime] = None


class OddsContextV2(V2IngestModel):
    home_win: Optional[OddsMarketV2] = None
    draw: Optional[OddsMarketV2] = None
    away_win: Optional[OddsMarketV2] = None
    home_not_lose: Optional[OddsMarketV2] = None
    away_not_lose: Optional[OddsMarketV2] = None
    btts_yes: Optional[OddsMarketV2] = None
    home_team_to_score: Optional[OddsMarketV2] = None
    away_team_to_score: Optional[OddsMarketV2] = None
    over_15: Optional[OddsMarketV2] = None
    odds_confidence: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# 7. News
# ---------------------------------------------------------------------------


class NewsItemV2(V2IngestModel):
    title: str
    summary: Optional[str] = None
    severity: NewsSeverity = NewsSeverity.MEDIUM
    source: Optional[str] = None
    published_at: Optional[datetime] = None
    relevance_score: float = Field(ge=0.0, le=1.0, default=0.5)


class NewsContextV2(V2IngestModel):
    major_news_items: List[NewsItemV2] = Field(default_factory=list)
    locker_room_issues: List[str] = Field(default_factory=list)
    important_returnees: List[str] = Field(default_factory=list)
    priority_signals: List[str] = Field(default_factory=list)
    fatigue_signals: List[str] = Field(default_factory=list)
    rotation_signals: List[str] = Field(default_factory=list)
    news_risk_score: float = Field(ge=0.0, le=1.0, default=0.0)


# ---------------------------------------------------------------------------
# 8. Schedule (full window)
# ---------------------------------------------------------------------------


class ScheduleMatchContextV2(V2IngestModel):
    competition: CompetitionRefV2
    match_date: date
    importance: MatchImportance = MatchImportance.MEDIUM
    opponent_name: str
    opponent_strength_band: OpponentStrengthBand = OpponentStrengthBand.MEDIUM
    is_home: bool = True
    travel_load_score: float = Field(ge=0.0, le=1.0, default=0.0)


class ScheduleContextV2(V2IngestModel):
    team: TeamRefV2
    days_since_last_match: Optional[int] = None
    days_to_next_match: Optional[int] = None
    matches_last_14_days: int = 0
    matches_next_7_days: int = 0
    prev_match: Optional[ScheduleMatchContextV2] = None
    next_match: Optional[ScheduleMatchContextV2] = None
    fixture_window_difficulty_score: float = Field(ge=0.0, le=1.0, default=0.0)
    travel_load_score: float = Field(ge=0.0, le=1.0, default=0.0)
    fixture_congestion_score: float = Field(ge=0.0, le=1.0, default=0.0)
    rotation_risk_score: float = Field(ge=0.0, le=1.0, default=0.0)
    pre_big_match_preservation_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    post_big_match_relaxation_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    emotional_swing_score: float = Field(ge=0.0, le=1.0, default=0.0)


# ---------------------------------------------------------------------------
# 9. H2H
# ---------------------------------------------------------------------------


class H2HContextV2(V2IngestModel):
    team_h2h_total_matches: int = Field(ge=0, default=0)
    team_h2h_recent_score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Recent H2H from home team perspective.",
    )
    team_h2h_home_away_split: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Home/away split bias in H2H.",
    )
    h2h_btts_rate: float = Field(ge=0.0, le=1.0, default=0.5)
    h2h_over25_rate: float = Field(ge=0.0, le=1.0, default=0.5)
    h2h_context_bias: float = Field(
        default=0.0,
        ge=-0.3,
        le=0.3,
        description="Weak 1X2 contextual bias (blueprint).",
    )


# ---------------------------------------------------------------------------
# 10. Confidence
# ---------------------------------------------------------------------------


class ConfidenceBreakdownV2(V2IngestModel):
    match_meta_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    teams_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    squads_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    coaches_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    odds_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    news_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    schedule_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    h2h_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    data_freshness_score: float = Field(ge=0.0, le=1.0, default=0.5)
    source_agreement_score: float = Field(ge=0.0, le=1.0, default=0.5)
    overall_completeness_score: float = Field(ge=0.0, le=1.0, default=0.5)
    overall_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# 11. Match snapshot (collector/normalizer output)
# ---------------------------------------------------------------------------


class MatchAnalysisSnapshotV2(V2IngestModel):
    match_meta: MatchMetaV2
    home_team_context: TeamContextV2
    away_team_context: TeamContextV2
    home_squad: SquadContextV2
    away_squad: SquadContextV2
    home_coach: CoachContextV2
    away_coach: CoachContextV2
    home_schedule: ScheduleContextV2
    away_schedule: ScheduleContextV2
    odds: OddsContextV2 = Field(default_factory=OddsContextV2)
    news_context: NewsContextV2 = Field(default_factory=NewsContextV2)
    h2h_context: H2HContextV2 = Field(default_factory=H2HContextV2)
    confidence: ConfidenceBreakdownV2 = Field(default_factory=ConfidenceBreakdownV2)
    collected_at: datetime = Field(default_factory=_utc_now)
    updated_at: Optional[datetime] = None
    source_tags: List[str] = Field(default_factory=list)
    snapshot_version: str = "2.0.0"


# ---------------------------------------------------------------------------
# 12. Scorer outputs
# ---------------------------------------------------------------------------


class LeagueFactorScoresV2(V2OutputModel):
    baseline_strength: float = Field(ge=0.0, le=1.0, default=0.5)
    current_form: float = Field(ge=0.0, le=1.0, default=0.5)
    motivation: float = Field(ge=0.0, le=1.0, default=0.5)
    squad_availability: float = Field(ge=0.0, le=1.0, default=0.5)
    coach_factor: float = Field(ge=0.0, le=1.0, default=0.5)
    schedule_context: float = Field(ge=0.0, le=1.0, default=0.5)
    h2h_context_bias: float = Field(
        default=0.0,
        ge=-0.3,
        le=0.3,
        description="Signed weak bias applied in team total score.",
    )
    total_score: float = Field(ge=0.0, le=1.0, default=0.5)


class TeamScoringResultV2(V2OutputModel):
    team: TeamRefV2
    factor_scores: LeagueFactorScoresV2 = Field(default_factory=LeagueFactorScoresV2)
    summary_flags: List[str] = Field(default_factory=list)


class MarketPredictionV2(V2OutputModel):
    market_key: str
    probability: float = Field(ge=0.0, le=1.0)
    fair_odds: Optional[float] = Field(default=None, gt=1.0)
    book_odds: Optional[float] = Field(default=None, gt=1.0)
    edge: Optional[float] = Field(
        default=None,
        description="Expected-value edge vs book (may be negative).",
    )
    label: str = ""


class ExpressScreeningV2(V2OutputModel):
    safety_class: ExpressSafetyClass = ExpressSafetyClass.EXPRESS_CAUTION
    penalty_score: float = Field(ge=0.0, le=1.0, default=0.0)
    reasons: List[str] = Field(default_factory=list)
    allow_for_express: bool = Field(
        default=False,
        description="Set True only when scorer explicitly allows express inclusion.",
    )


AnalysisMode = Literal["full_scoring", "analysis_only"]
PredictionMode = Literal["league_scored", "parked_analysis_only"]
ParkedRouteKind = Literal["non_league_parked", "unknown_parked"]


class ParkedAnalysisContextV2(V2OutputModel):
    """Structured metadata when league scorer is intentionally not applied."""

    mode: Literal["analysis_only"] = "analysis_only"
    route: ParkedRouteKind
    tournament_type: TournamentType
    category: str = Field(description="CompetitionContextClass value, e.g. domestic_cup.")
    reason: str
    book_odds_available: bool = False
    book_odds_markets_count: int = Field(ge=0, default=0)
    news_available: bool = False
    standings_available: bool = False
    snapshot_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    snapshot_completeness: float = Field(ge=0.0, le=1.0, default=0.5)
    can_build_express: bool = False
    data_quality_note: Optional[str] = None


class MatchPredictionResultV2(V2OutputModel):
    match_meta: MatchMetaV2
    home_scoring: TeamScoringResultV2
    away_scoring: TeamScoringResultV2
    market_predictions: List[MarketPredictionV2] = Field(default_factory=list)
    best_market: Optional[MarketPredictionV2] = None
    express_safety: ExpressScreeningV2 = Field(default_factory=ExpressScreeningV2)
    prediction_summary: Optional[str] = None
    overall_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    analysis_mode: AnalysisMode = "full_scoring"
    prediction_mode: PredictionMode = "league_scored"
    parked_context: Optional[ParkedAnalysisContextV2] = None


class ExpressEventV2(V2OutputModel):
    match_meta: MatchMetaV2
    market_key: str
    probability: float = Field(ge=0.0, le=1.0)
    book_odds: float = Field(gt=1.0)
    label: str = ""
    edge: Optional[float] = None


class ExpressBetV2(V2OutputModel):
    events: List[ExpressEventV2] = Field(default_factory=list)
    total_odds: float = Field(gt=1.0)
    total_probability: float = Field(ge=0.0, le=1.0)
    target_odds: float = Field(gt=1.0)
    within_tolerance: bool = False
    selection_notes: str = ""


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    home = TeamRefV2(team_id=1, name="Arsenal FC", short_name="Arsenal")
    away = TeamRefV2(team_id=2, name="Chelsea FC", short_name="Chelsea")
    meta = MatchMetaV2(
        match_id=100,
        season=2024,
        competition_name="Premier League",
        competition_code="PL",
        match_date_utc=datetime(2024, 4, 25, 15, 0, tzinfo=timezone.utc),
        home_team=home,
        away_team=away,
    )
    snapshot = MatchAnalysisSnapshotV2(
        match_meta=meta,
        home_team_context=TeamContextV2(team=home),
        away_team_context=TeamContextV2(team=away),
        home_squad=SquadContextV2(team=home),
        away_squad=SquadContextV2(team=away),
        home_coach=CoachContextV2(coach=CoachRefV2(name="Coach A"), team=home),
        away_coach=CoachContextV2(coach=CoachRefV2(name="Coach B"), team=away),
        home_schedule=ScheduleContextV2(team=home),
        away_schedule=ScheduleContextV2(team=away),
    )
    assert snapshot.collected_at.tzinfo is not None
    print(snapshot.model_dump_json(indent=2)[:500])
    print("models_v2 OK")
