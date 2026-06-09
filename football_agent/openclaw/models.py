"""
Internal contract for data delivered by OpenClaw (or replay fixtures).

Structured for fail-soft ingestion: nested blocks stay optional so partial JSON
parses cleanly. Prefer explicit fields here over dumping opaque dict blobs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from football_agent.domain.enums_v2 import (
    NewsSeverity,
    TournamentType,
)


class OpenClawBaseModel(BaseModel):
    """Drop unknown keys from collector output without failing."""

    model_config = ConfigDict(extra="ignore")


# --- Source -----------------------------------------------------------------


class OpenClawSourceMetadata(OpenClawBaseModel):
    """Provenance / quality hints from collector (optional)."""

    source_name: Optional[str] = Field(default=None, description="e.g. openclaw-tournament-pl")
    collected_at: Optional[datetime] = Field(default=None, description="UTC when payload was finalized")
    data_freshness_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How fresh the stitched data is perceived to be.",
    )
    completeness_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Rough coverage of requested blocks.",
    )
    confidence_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Upstream trust / agreement score.",
    )
    tags: List[str] = Field(default_factory=list)


# --- IDs & meta ------------------------------------------------------------


class OpenClawExternalIds(OpenClawBaseModel):
    """Cross-source reconciliation (no format enforced)."""

    openclaw_event_id: Optional[str] = None
    fixture_id_api_football: Optional[int] = None
    fixture_id_football_data: Optional[int] = None
    slug: Optional[str] = None
    urls: Dict[str, str] = Field(default_factory=dict)


class OpenClawVenue(OpenClawBaseModel):
    name: Optional[str] = None
    is_neutral: bool = False


class OpenClawMatchMeta(OpenClawBaseModel):
    internal_match_id_hint: Optional[int] = Field(
        default=None,
        description="If known, used as snapshot match_id; else derived from external ids.",
    )
    external_ids: OpenClawExternalIds = Field(default_factory=OpenClawExternalIds)
    season: Optional[int] = None
    match_date_utc: Optional[datetime] = Field(default=None)
    timezone_note: Optional[str] = Field(default=None, description="Display only.")
    venue: Optional[OpenClawVenue] = None
    competition_name: Optional[str] = Field(default=None, description='e.g. "Premier League"')
    competition_code: Optional[str] = Field(default=None, description='e.g. "PL" when known.')
    tournament_type: Optional[TournamentType] = None
    stage: Optional[str] = None
    round_number: Optional[int] = Field(default=None, ge=1)
    country: Optional[str] = None


# --- Teams & table -----------------------------------------------------------


class OpenClawTeamRef(OpenClawBaseModel):
    team_id: Optional[int] = None
    name: str = ""
    short_name: Optional[str] = None
    country: Optional[str] = None


class OpenClawTableContext(OpenClawBaseModel):
    """Lightweight standings slice for motivation / snapshots."""

    position: Optional[int] = Field(default=None, ge=1)
    points: Optional[int] = None
    played: Optional[int] = None
    goal_difference: Optional[int] = None
    form_string: Optional[str] = Field(
        default=None,
        description="Last N results e.g. WWDLL — adapter may derive form scores if omitted.",
    )
    gap_points_above_target: Optional[int] = None
    gap_points_below_target: Optional[int] = None


class OpenClawFormBlock(OpenClawBaseModel):
    last_5_form_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    last_10_form_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    home_form_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    away_form_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    form_under_coach_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    performance_trend_score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


class OpenClawTeamMiniSchedule(OpenClawBaseModel):
    fixture_congestion_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    rotation_risk_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    pre_big_match_preservation_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    post_big_match_relaxation_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OpenClawTeamContextBlock(OpenClawBaseModel):
    team: OpenClawTeamRef
    baseline_strength_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    motivation_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    availability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bench_quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    line_stability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    form: Optional[OpenClawFormBlock] = None
    mini_schedule: Optional[OpenClawTeamMiniSchedule] = None
    table: Optional[OpenClawTableContext] = None


# --- Squad / availability ----------------------------------------------------


class OpenClawPlayerRef(OpenClawBaseModel):
    player_id: Optional[int] = None
    name: str = ""
    position: Optional[str] = None
    shirt_number: Optional[int] = None


class OpenClawPlayerAvailability(OpenClawBaseModel):
    player: OpenClawPlayerRef
    status: Literal["AVAILABLE", "DOUBTFUL", "SUSPENDED", "INJURED", "UNKNOWN"] = "UNKNOWN"
    importance: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    reason: Optional[str] = None
    expected_return_date: Optional[date] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OpenClawSquadBlock(OpenClawBaseModel):
    expected_starting_xi: List[OpenClawPlayerRef] = Field(default_factory=list)
    bench_players: List[OpenClawPlayerRef] = Field(default_factory=list)
    unavailable: List[OpenClawPlayerAvailability] = Field(default_factory=list)
    doubtful: List[OpenClawPlayerAvailability] = Field(default_factory=list)
    suspended: List[OpenClawPlayerAvailability] = Field(default_factory=list)
    starting_xi_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    line_stability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# --- Coach -------------------------------------------------------------------


class OpenClawCoachBlock(OpenClawBaseModel):
    coach_id: Optional[int] = None
    name: str = ""
    nationality: Optional[str] = None
    coach_start_date: Optional[date] = None
    days_in_charge: Optional[int] = None
    matches_in_charge: Optional[int] = None
    is_first_match: bool = False
    is_new_coach_bounce_window: bool = False
    tenure_phase_hint: Optional[Literal["ESTABLISHED", "FIRST_MATCH", "BOUNCE_WINDOW"]] = None
    coach_global_strength_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    coach_rotation_tendency_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source_quality_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Feeds coaches_confidence in snapshot breakdown.",
    )


# --- Odds (first-batch keys) -----------------------------------------------


class OpenClawOddsMarkets(OpenClawBaseModel):
    """Numeric prices only (>1); adapter maps into OddsMarketV2."""

    home_win: Optional[float] = Field(default=None, gt=1.0)
    draw: Optional[float] = Field(default=None, gt=1.0)
    away_win: Optional[float] = Field(default=None, gt=1.0)
    home_not_lose: Optional[float] = Field(default=None, gt=1.0)
    away_not_lose: Optional[float] = Field(default=None, gt=1.0)
    btts_yes: Optional[float] = Field(default=None, gt=1.0)
    home_team_to_score: Optional[float] = Field(default=None, gt=1.0)
    away_team_to_score: Optional[float] = Field(default=None, gt=1.0)
    over_15: Optional[float] = Field(default=None, gt=1.0)
    bookmaker: Optional[str] = None
    collected_at: Optional[datetime] = None


# --- H2H ---------------------------------------------------------------------


class OpenClawH2HBlock(OpenClawBaseModel):
    team_h2h_total_matches: Optional[int] = Field(default=None, ge=0)
    team_h2h_recent_score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    team_h2h_home_away_split: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    h2h_btts_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    h2h_over25_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    h2h_context_bias: Optional[float] = Field(default=None, ge=-0.3, le=0.3)
    recent_meetings_notes: List[str] = Field(default_factory=list)


# --- Schedule window ---------------------------------------------------------


class OpenClawScheduleMatchStub(OpenClawBaseModel):
    competition_code: str = "UNK"
    competition_name: str = "Unknown"
    match_date: Optional[date] = None
    opponent_name: str = "Opponent"
    is_home: bool = True
    importance: Optional[str] = None


class OpenClawScheduleBlock(OpenClawBaseModel):
    days_since_last_match: Optional[int] = None
    days_to_next_match: Optional[int] = None
    matches_last_14_days: Optional[int] = Field(default=None, ge=0)
    matches_next_7_days: Optional[int] = Field(default=None, ge=0)
    prev_match: Optional[OpenClawScheduleMatchStub] = None
    next_match: Optional[OpenClawScheduleMatchStub] = None
    fixture_window_difficulty_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    travel_load_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fixture_congestion_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    rotation_risk_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    pre_big_match_preservation_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    post_big_match_relaxation_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    emotional_swing_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# --- News --------------------------------------------------------------------


class OpenClawNewsItem(OpenClawBaseModel):
    title: str = ""
    summary: Optional[str] = None
    severity: Optional[NewsSeverity] = None
    source: Optional[str] = None
    published_at: Optional[datetime] = None
    relevance_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)


class OpenClawNewsBlock(OpenClawBaseModel):
    items: List[OpenClawNewsItem] = Field(default_factory=list)
    locker_room_issues: List[str] = Field(default_factory=list)
    priority_signals: List[str] = Field(default_factory=list)
    fatigue_signals: List[str] = Field(default_factory=list)
    rotation_signals: List[str] = Field(default_factory=list)
    important_returnees: List[str] = Field(default_factory=list)
    news_risk_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# --- Root payload ------------------------------------------------------------


class OpenClawMatchPayload(OpenClawBaseModel):
    """
    Full match bundle from OpenClaw collector.

    Parse from JSON with ``OpenClawMatchPayload.model_validate(data)``.
    """

    schema_version: str = Field(default="openclaw.match.v1", description="Bumped when contract breaks.")
    source: Optional[OpenClawSourceMetadata] = None

    meta: Optional[OpenClawMatchMeta] = None

    side: Literal["standalone", "home", "away"] = "standalone"
    home_team: Optional[OpenClawTeamRef] = None
    away_team: Optional[OpenClawTeamRef] = None

    home_context: Optional[OpenClawTeamContextBlock] = None
    away_context: Optional[OpenClawTeamContextBlock] = None

    home_squad: Optional[OpenClawSquadBlock] = None
    away_squad: Optional[OpenClawSquadBlock] = None

    home_coach: Optional[OpenClawCoachBlock] = None
    away_coach: Optional[OpenClawCoachBlock] = None

    home_schedule: Optional[OpenClawScheduleBlock] = None
    away_schedule: Optional[OpenClawScheduleBlock] = None

    odds: Optional[OpenClawOddsMarkets] = None
    h2h: Optional[OpenClawH2HBlock] = None
    news: Optional[OpenClawNewsBlock] = None

    # Extension hook for experiments (ignored by default adapter fields)
    extensions: Dict[str, Any] = Field(default_factory=dict)
