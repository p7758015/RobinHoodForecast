"""Structured match news / coach enrichment models (Brave + OpenClaw phase)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CoachStatus = Literal["active", "interim", "caretaker", "suspended", "absent", "unknown"]
CoachPrioritySignal = Literal[
    "league_priority",
    "cup_priority",
    "rotation_expected",
    "must_win_language",
    "morale_pressure",
    "none",
]
NewsFreshnessStatus = Literal["fresh", "stale", "unknown"]
ReliabilityLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class NewsContextBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NewsSourceRef(NewsContextBase):
    title: str
    url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    snippet: Optional[str] = None
    reliability: ReliabilityLevel = "UNKNOWN"
    topic_tags: List[str] = Field(default_factory=list)


class CoachContextBlock(NewsContextBase):
    # Identity / status
    home_coach_name: Optional[str] = None
    away_coach_name: Optional[str] = None
    home_coach_status: CoachStatus = "unknown"
    away_coach_status: CoachStatus = "unknown"
    home_coach_tenure_days: Optional[int] = None
    away_coach_tenure_days: Optional[int] = None

    # Pre-match signals
    home_coach_recent_quotes: List[str] = Field(default_factory=list)
    away_coach_recent_quotes: List[str] = Field(default_factory=list)
    home_coach_rotation_signal: Optional[str] = None
    away_coach_rotation_signal: Optional[str] = None
    home_coach_morale_signal: Optional[str] = None
    away_coach_morale_signal: Optional[str] = None
    home_coach_tactical_signal: Optional[str] = None
    away_coach_tactical_signal: Optional[str] = None
    home_coach_absence_signal: Optional[str] = None
    away_coach_absence_signal: Optional[str] = None
    coach_fixture_congestion_comment: Optional[str] = None
    coach_priority_signal: CoachPrioritySignal = "none"

    # Coach vs coach history
    coach_h2h_total_matches: Optional[int] = None
    coach_h2h_home_wins: Optional[int] = None
    coach_h2h_away_wins: Optional[int] = None
    coach_h2h_draws: Optional[int] = None
    coach_h2h_goal_diff: Optional[int] = None
    coach_h2h_last_meeting_date: Optional[str] = None
    coach_h2h_recent_summary: Optional[str] = None
    coach_h2h_confidence: ReliabilityLevel = "UNKNOWN"

    # Coach vs opponent team history
    home_coach_vs_away_team_matches: Optional[int] = None
    home_coach_vs_away_team_wins: Optional[int] = None
    away_coach_vs_home_team_matches: Optional[int] = None
    away_coach_vs_home_team_wins: Optional[int] = None

    # Quality
    coach_news_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coach_news_freshness: NewsFreshnessStatus = "unknown"
    coach_context_sources: List[NewsSourceRef] = Field(default_factory=list)
    coach_context_generated_at_utc: Optional[datetime] = None
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class GeneralNewsBlock(NewsContextBase):
    injuries_signals: List[str] = Field(default_factory=list)
    suspension_signals: List[str] = Field(default_factory=list)
    predicted_lineup_signals: List[str] = Field(default_factory=list)
    locker_room_signals: List[str] = Field(default_factory=list)
    motivation_signals: List[str] = Field(default_factory=list)
    schedule_pressure_signals: List[str] = Field(default_factory=list)
    derby_or_rivalry_signal: Optional[str] = None
    weather_or_travel_signal: Optional[str] = None
    general_news_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    general_news_sources: List[NewsSourceRef] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MatchNewsContext(NewsContextBase):
    """Enrichment-only news/coach block (additive; never overrides factual Flashscore fields)."""

    match_id: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    source_backend: str = "brave_search"

    coach: CoachContextBlock = Field(default_factory=CoachContextBlock)
    general_news: GeneralNewsBlock = Field(default_factory=GeneralNewsBlock)

    sources: List[NewsSourceRef] = Field(default_factory=list)
    collected_at_utc: Optional[datetime] = None
    freshest_source_at_utc: Optional[datetime] = None
    source_count: int = 0
    is_stale: bool = False
    freshness_status: NewsFreshnessStatus = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
