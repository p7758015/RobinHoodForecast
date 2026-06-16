"""Typed unified pre-snapshot contract (no scorer, no MatchAnalysisSnapshotV2)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from football_agent.flashscore.derived_season import LeagueTableMotivationContext
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.models import MatchOddsContext
from football_agent.news_context.models import MatchNewsContext
from football_agent.openclaw_context.models import OpenClawMatchContext


MatchLinkStrategy = Literal[
    "by_match_id",
    "by_teams_and_date",
    "by_query_string",
    "provided_without_link",
    "unlinked",
]


class MergeBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)


class MergedHeadline(MergeBaseModel):
    """Shallow read-model block for consumers (avoid deep tree navigation)."""

    home_team: str
    away_team: str
    competition_name: Optional[str] = None
    kickoff_utc: Optional[datetime] = None

    season_phase: Optional[str] = None
    gap_to_title_points: Optional[int] = None
    gap_to_europe_points: Optional[int] = None
    gap_to_relegation_safety_points: Optional[int] = None

    openclaw_context_present: bool = False

    odds_present: bool = False
    odds_missing_count: int = 0

    home_win_odds: Optional[float] = None
    away_win_odds: Optional[float] = None
    double_chance_1x_odds: Optional[float] = None
    double_chance_x2_odds: Optional[float] = None
    btts_yes_odds: Optional[float] = None
    home_team_to_score_yes_odds: Optional[float] = None
    away_team_to_score_yes_odds: Optional[float] = None
    over_1_5_odds: Optional[float] = None
    under_3_5_odds: Optional[float] = None


class MergeProvenance(MergeBaseModel):
    """
    Merge-level provenance and warnings.

    Important:
    - `match_link_strategy` refers ONLY to OpenClaw context linking.
    - `odds_link_strategy` refers ONLY to odds linking.
    These strategies are independent and must not override each other.
    """

    match_link_strategy: MatchLinkStrategy = "unlinked"
    odds_link_strategy: MatchLinkStrategy = "unlinked"
    blocks_present: List[str] = Field(default_factory=list)
    missing_blocks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MergedMatchAnalysisContext(MergeBaseModel):
    """
    Unified analysis context (pre-snapshot).

    Holds:
    - raw Flashscore factual layer (typed)
    - derived season/table motivation (typed)
    - optional OpenClaw context layer (typed)

    This model is NOT a MatchAnalysisSnapshotV2 and does NOT invoke scorers.
    """

    headline: MergedHeadline
    flashscore_facts: FlashscoreMatchFacts
    derived_season_motivation: LeagueTableMotivationContext
    openclaw_context: Optional[OpenClawMatchContext] = None
    odds_context: Optional[MatchOddsContext] = None
    news_context: Optional[MatchNewsContext] = None
    provenance: MergeProvenance

