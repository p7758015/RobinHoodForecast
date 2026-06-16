"""Typed contract for normalized Flashscore factual data (league matches)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from football_agent.domain.enums_v2 import TournamentType


class FlashscoreBaseModel(BaseModel):
    """Base for Flashscore ingest models: ignore unknown fields from scraper."""

    model_config = ConfigDict(extra="ignore")


class FlashscoreMeta(FlashscoreBaseModel):
    match_id: str
    source_url: str
    source: str = Field(default="flashscore")

    competition_name: str
    competition_country: Optional[str] = None
    season: Optional[int] = None
    stage: Optional[str] = None
    round: Optional[str] = None
    tournament_type: TournamentType = TournamentType.LEAGUE_REGULAR

    kickoff_utc: Optional[datetime] = None

    home_team_name: str
    away_team_name: str
    status: str = Field(default="SCHEDULED", description="Raw status string from Flashscore.")


class FlashscoreStandings(FlashscoreBaseModel):
    home_position: Optional[int] = None
    away_position: Optional[int] = None

    home_points: Optional[int] = None
    away_points: Optional[int] = None

    home_matches_played: Optional[int] = None
    away_matches_played: Optional[int] = None

    home_goal_difference: Optional[int] = None
    away_goal_difference: Optional[int] = None

    home_home_table_position: Optional[int] = None
    away_away_table_position: Optional[int] = None

    home_games_in_hand: Optional[int] = None
    away_games_in_hand: Optional[int] = None


class FlashscoreSeasonContextInputs(FlashscoreBaseModel):
    """Raw table/calendar inputs — no derived motivation logic yet."""

    matchday_number: Optional[int] = None
    total_matchdays: Optional[int] = None
    rounds_remaining_after_this_match: Optional[int] = None

    # Free-form fields for future title/europe/relegation derivation.
    table_neighbors: Dict[str, int] = Field(
        default_factory=dict,
        description="Optional pre-parsed neighbors or thresholds (e.g. 'ucl_cutoff_pos': 4).",
    )
    relevant_thresholds: Dict[str, int] = Field(default_factory=dict)


class FlashscoreTeamFormBlock(FlashscoreBaseModel):
    last_n_results: List[str] = Field(
        default_factory=list,
        description="Sequence of 'W'/'D'/'L' from most recent backwards.",
    )
    last_n_points: Optional[int] = None
    goals_for_last_n: Optional[int] = None
    goals_against_last_n: Optional[int] = None
    clean_sheets_last_n: Optional[int] = None
    btts_last_n: Optional[int] = None
    over_25_last_n: Optional[int] = None

    home_only_form: Optional[List[str]] = None
    away_only_form: Optional[List[str]] = None


class FlashscoreFormBlock(FlashscoreBaseModel):
    home: Optional[FlashscoreTeamFormBlock] = None
    away: Optional[FlashscoreTeamFormBlock] = None


class FlashscoreH2HBlock(FlashscoreBaseModel):
    recent_h2h_matches: int = 0
    home_h2h_wins: int = 0
    away_h2h_wins: int = 0
    h2h_draws: int = 0
    avg_h2h_goals: Optional[float] = None
    btts_h2h_rate: Optional[float] = None
    venue_specific_h2h: Optional[Dict[str, int]] = None


class FlashscoreSquadRaw(FlashscoreBaseModel):
    predicted_lineups: Dict[str, List[str]] = Field(default_factory=dict)
    confirmed_lineups: Dict[str, List[str]] = Field(default_factory=dict)
    formations: Dict[str, Optional[str]] = Field(default_factory=dict)
    bench: Dict[str, List[str]] = Field(default_factory=dict)

    missing_players_raw: Dict[str, List[Any]] = Field(default_factory=dict)
    player_status_raw: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    coach_name_home: Optional[str] = None
    coach_name_away: Optional[str] = None


class FlashscoreScheduleRaw(FlashscoreBaseModel):
    previous_match_date_home: Optional[date] = None
    previous_match_date_away: Optional[date] = None

    next_match_date_home: Optional[date] = None
    next_match_date_away: Optional[date] = None

    recent_match_dates_home: List[date] = Field(default_factory=list)
    recent_match_dates_away: List[date] = Field(default_factory=list)


class FlashscoreStatsRaw(FlashscoreBaseModel):
    """Container for raw historical / match stats / incidents as provided by scraper."""

    team_stats: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    match_stats: Dict[str, float] = Field(default_factory=dict)
    incidents: List[Dict[str, str]] = Field(default_factory=list)


class FlashscoreProvenance(FlashscoreBaseModel):
    scraper_backend_name: str
    scraper_backend_version: Optional[str] = None
    adapter_version: str = "flashscore-facts-v1"
    collected_at_utc: Optional[datetime] = None

    blocks_present: List[str] = Field(default_factory=list)
    missing_blocks: List[str] = Field(default_factory=list)
    parsing_warnings: List[str] = Field(default_factory=list)


class FlashscoreMatchFacts(FlashscoreBaseModel):
    """Top-level normalized facts for a single Flashscore match."""

    meta: FlashscoreMeta
    standings: Optional[FlashscoreStandings] = None
    season_context_inputs: Optional[FlashscoreSeasonContextInputs] = None
    form: Optional[FlashscoreFormBlock] = None
    h2h: Optional[FlashscoreH2HBlock] = None
    squad_raw: Optional[FlashscoreSquadRaw] = None
    schedule_raw: Optional[FlashscoreScheduleRaw] = None
    stats_raw: Optional[FlashscoreStatsRaw] = None
    provenance: FlashscoreProvenance

