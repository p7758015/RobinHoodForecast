"""Typed contract for normalized OpenClaw match context (secondary context/news layer)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OpenClawContextBaseModel(BaseModel):
    """Fail-soft ingest model: ignore unknown keys from external extractors."""

    model_config = ConfigDict(extra="ignore")


ReliabilityLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
TeamSide = Literal["HOME", "AWAY", "BOTH", "UNKNOWN"]
DepthRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
RotationRiskLevel = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
PlayerStatus = Literal["OUT", "DOUBTFUL", "RETURNING", "SUSPENDED", "ROTATION_RISK", "UNKNOWN"]


class OpenClawContextMeta(OpenClawContextBaseModel):
    match_id: Optional[str] = None
    source: str = Field(default="openclaw")

    query_home_team: str
    query_away_team: str
    query_home_team_normalized: Optional[str] = None
    query_away_team_normalized: Optional[str] = None

    query_competition_name: Optional[str] = None
    query_kickoff_utc: Optional[datetime] = None
    query_date: Optional[date] = None
    query_string: Optional[str] = None

    collected_at_utc: datetime
    context_window_hours: Optional[int] = None


class OpenClawNewsItem(OpenClawContextBaseModel):
    title: str
    summary: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    reliability_level: ReliabilityLevel = "UNKNOWN"
    topic_tags: List[str] = Field(default_factory=list)
    affects_team: TeamSide = "UNKNOWN"


class OpenClawNewsBlock(OpenClawContextBaseModel):
    """News items split by side + optional aggregates."""

    home_news_items: List[OpenClawNewsItem] = Field(default_factory=list)
    away_news_items: List[OpenClawNewsItem] = Field(default_factory=list)
    match_news_items: List[OpenClawNewsItem] = Field(default_factory=list)

    source_count: Optional[int] = None
    high_confidence_count: Optional[int] = None
    conflicting_reports_flag: Optional[bool] = None


class OpenClawPlayerContextItem(OpenClawContextBaseModel):
    player_name: str
    reason: Optional[str] = None
    expected_impact: Optional[str] = None
    status: PlayerStatus = "UNKNOWN"
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    confidence: ReliabilityLevel = "UNKNOWN"


class OpenClawSquadSideContext(OpenClawContextBaseModel):
    missing_players_context: List[OpenClawPlayerContextItem] = Field(default_factory=list)
    returning_players_context: List[OpenClawPlayerContextItem] = Field(default_factory=list)
    expected_rotation_notes: List[str] = Field(default_factory=list)
    lineup_uncertainty_notes: List[str] = Field(default_factory=list)
    suspension_notes: List[str] = Field(default_factory=list)
    injury_notes: List[str] = Field(default_factory=list)
    depth_risk_level: DepthRiskLevel = "UNKNOWN"
    rotation_risk_level: RotationRiskLevel = "UNKNOWN"

    source_count: Optional[int] = None
    high_confidence_count: Optional[int] = None
    conflicting_reports_flag: Optional[bool] = None


class OpenClawSquadContext(OpenClawContextBaseModel):
    home: OpenClawSquadSideContext = Field(default_factory=OpenClawSquadSideContext)
    away: OpenClawSquadSideContext = Field(default_factory=OpenClawSquadSideContext)


class OpenClawCoachSideContext(OpenClawContextBaseModel):
    coach_name: Optional[str] = None
    tenure_summary: Optional[str] = None
    trophies_summary: Optional[str] = None
    style_summary: Optional[str] = None
    pressure_summary: Optional[str] = None
    recent_change_flag: Optional[bool] = None
    influence_summary: Optional[str] = None


class OpenClawCoachMatchupContext(OpenClawContextBaseModel):
    coach_vs_coach_summary: Optional[str] = None
    coach_vs_opponent_team_summary: Optional[str] = None
    previous_meetings_summary: Optional[str] = None
    confidence: ReliabilityLevel = "UNKNOWN"


class OpenClawCoachContext(OpenClawContextBaseModel):
    home: OpenClawCoachSideContext = Field(default_factory=OpenClawCoachSideContext)
    away: OpenClawCoachSideContext = Field(default_factory=OpenClawCoachSideContext)
    matchup: OpenClawCoachMatchupContext = Field(default_factory=OpenClawCoachMatchupContext)


class OpenClawMotivationNarrativeSide(OpenClawContextBaseModel):
    primary_objective_summary: Optional[str] = None
    pressure_summary: Optional[str] = None
    rivalry_summary: Optional[str] = None
    revenge_angle_summary: Optional[str] = None
    must_win_narrative: Optional[str] = None
    distraction_risk_summary: Optional[str] = None
    public_narrative_summary: Optional[str] = None
    confidence: ReliabilityLevel = "UNKNOWN"


class OpenClawMotivationNarrative(OpenClawContextBaseModel):
    home: OpenClawMotivationNarrativeSide = Field(default_factory=OpenClawMotivationNarrativeSide)
    away: OpenClawMotivationNarrativeSide = Field(default_factory=OpenClawMotivationNarrativeSide)
    matchwide: OpenClawMotivationNarrativeSide = Field(default_factory=OpenClawMotivationNarrativeSide)


class OpenClawFatigueScheduleSide(OpenClawContextBaseModel):
    fatigue_summary: Optional[str] = None
    travel_summary: Optional[str] = None
    rotation_expectation_summary: Optional[str] = None
    post_europe_risk_summary: Optional[str] = None
    sandwich_match_risk_summary: Optional[str] = None
    confidence: ReliabilityLevel = "UNKNOWN"


class OpenClawFatigueScheduleContext(OpenClawContextBaseModel):
    home: OpenClawFatigueScheduleSide = Field(default_factory=OpenClawFatigueScheduleSide)
    away: OpenClawFatigueScheduleSide = Field(default_factory=OpenClawFatigueScheduleSide)


class OpenClawContextProvenance(OpenClawContextBaseModel):
    backend_name: str
    backend_version: Optional[str] = None
    adapter_version: str = "openclaw-context-v1"
    collected_at_utc: datetime
    blocks_present: List[str] = Field(default_factory=list)
    missing_blocks: List[str] = Field(default_factory=list)
    extraction_warnings: List[str] = Field(default_factory=list)


class OpenClawMatchContext(OpenClawContextBaseModel):
    meta: OpenClawContextMeta
    news: Optional[OpenClawNewsBlock] = None
    squad_context: Optional[OpenClawSquadContext] = None
    coach_context: Optional[OpenClawCoachContext] = None
    motivation_narrative: Optional[OpenClawMotivationNarrative] = None
    fatigue_schedule_context: Optional[OpenClawFatigueScheduleContext] = None
    provenance: OpenClawContextProvenance

