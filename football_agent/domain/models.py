from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class Team(BaseModel):
    id: int
    name: str
    short_name: str


class Coach(BaseModel):
    id: int
    name: str


class CoachMatch(BaseModel):
    match_id: int
    team_id: int
    opponent_id: int
    result: str  # 'W', 'D', 'L'
    date: date


class StandingEntry(BaseModel):
    team: Team
    position: int
    points: int
    played_games: int
    won: int
    draw: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    form: str  # "W,W,D,L,W" — как приходит из API


class MatchResult(BaseModel):
    match_id: int
    date: date
    is_home: bool
    goals_for: int
    goals_against: int
    result: str  # 'W', 'D', 'L'
    coach_id: Optional[int] = None


class Match(BaseModel):
    id: int
    competition_code: str
    home_team: Team
    away_team: Team
    utc_date: datetime
    status: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    matchday: int


class Odds(BaseModel):
    fixture_id: int
    home_win: Optional[float] = None
    draw: Optional[float] = None
    away_win: Optional[float] = None
    home_not_lose: Optional[float] = None  # Double Chance 1X
    away_not_lose: Optional[float] = None  # Double Chance X2
    btts_yes: Optional[float] = None


class MarketPrediction(BaseModel):
    market: str  # 'HOME_WIN','AWAY_WIN','HOME_NOT_LOSE','AWAY_NOT_LOSE','BTTS_YES'
    probability: float
    odds: Optional[float] = None
    label: str


class TeamAnalysis(BaseModel):
    team: Team
    motivation: float
    form: float
    coach_strength: float
    rating: float
    eliminated: bool
    is_fighting: bool


class H2HStats(BaseModel):
    total_matches: int
    home_wins: int  # победы команды-хозяина текущего матча
    away_wins: int
    draws: int
    home_goals_avg: float
    away_goals_avg: float
    btts_rate: float  # доля матчей где обе забили
    over25_rate: float  # доля матчей с 3+ голами


class MatchAnalysisResult(BaseModel):
    match: Match
    home_analysis: TeamAnalysis
    away_analysis: TeamAnalysis
    h2h: H2HStats
    markets: List[MarketPrediction]
    best_market: MarketPrediction
    season_progress: float


class ExpressEvent(BaseModel):
    match: Match
    market: MarketPrediction


class ExpressBet(BaseModel):
    events: List[ExpressEvent]
    total_odds: float
    total_probability: float
    target_odds: float


if __name__ == "__main__":
    t = Team(id=1, name="Arsenal FC", short_name="Arsenal")
    print(t)
    print("models.py OK")
