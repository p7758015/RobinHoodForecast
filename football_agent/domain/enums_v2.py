"""Domain enumerations for RobinHoodForecast v2 (league match logic)."""

from __future__ import annotations

from enum import Enum


class TournamentType(str, Enum):
    """Competition format (blueprint §6.1)."""

    LEAGUE_REGULAR = "LEAGUE_REGULAR"
    DOMESTIC_CUP = "DOMESTIC_CUP"
    INTERNATIONAL_CLUB = "INTERNATIONAL_CLUB"
    INTERNATIONAL_NATIONAL = "INTERNATIONAL_NATIONAL"
    FRIENDLY = "FRIENDLY"
    UNKNOWN = "UNKNOWN"


class SeasonPhase(str, Enum):
    """Phase within the league season (blueprint §6.2)."""

    EARLY = "EARLY"
    MID = "MID"
    LATE = "LATE"
    FINAL_RUN_IN = "FINAL_RUN_IN"
    UNKNOWN = "UNKNOWN"


class MotivationContext(str, Enum):
    """Tournament meaning of the match for a team (blueprint §6.3)."""

    TITLE_RACE = "TITLE_RACE"
    EURO_RACE = "EURO_RACE"
    MIDTABLE_NEUTRAL = "MIDTABLE_NEUTRAL"
    RELEGATION_BATTLE = "RELEGATION_BATTLE"
    SAFE_NO_TARGET = "SAFE_NO_TARGET"


class MathematicalGoalStatus(str, Enum):
    """Whether a table target is still mathematically reachable."""

    SECURED = "SECURED"
    ACHIEVABLE = "ACHIEVABLE"
    UNLIKELY = "UNLIKELY"
    ELIMINATED = "ELIMINATED"
    NEUTRAL = "NEUTRAL"


class MatchImportance(str, Enum):
    """Relative importance of a fixture in the calendar window (blueprint §18)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class OpponentStrengthBand(str, Enum):
    """Coarse opponent quality band for schedule context."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    TOP = "TOP"


class PlayerImportance(str, Enum):
    """Impact tier if the player is unavailable (blueprint §5.3)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AvailabilityStatus(str, Enum):
    """Player availability for the match."""

    AVAILABLE = "AVAILABLE"
    DOUBTFUL = "DOUBTFUL"
    SUSPENDED = "SUSPENDED"
    INJURED = "INJURED"
    UNKNOWN = "UNKNOWN"


class CoachTenurePhase(str, Enum):
    """
    Stage of current coaching tenure (blueprint §18).

    Prefer setting `tenure_phase` in normalizers; keep `is_first_match` /
    `is_new_coach_bounce_window` for explicit rule overrides in scorers.
    """

    ESTABLISHED = "ESTABLISHED"
    FIRST_MATCH = "FIRST_MATCH"
    BOUNCE_WINDOW = "BOUNCE_WINDOW"  # typically matches 2–4 in charge


class NewsSeverity(str, Enum):
    """Impact tier of a news item on match context."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExpressSafetyClass(str, Enum):
    """Express suitability tier (blueprint §20)."""

    EXPRESS_SAFE = "EXPRESS_SAFE"
    EXPRESS_CAUTION = "EXPRESS_CAUTION"
    EXPRESS_AVOID = "EXPRESS_AVOID"


class LeagueMarketKey(str, Enum):
    """First-batch v2 league markets (scorer + express)."""

    HOME_WIN = "HOME_WIN"
    AWAY_WIN = "AWAY_WIN"
    HOME_NOT_LOSE = "HOME_NOT_LOSE"
    AWAY_NOT_LOSE = "AWAY_NOT_LOSE"
    BTTS_YES = "BTTS_YES"
    HOME_TEAM_TO_SCORE = "HOME_TEAM_TO_SCORE"
    AWAY_TEAM_TO_SCORE = "AWAY_TEAM_TO_SCORE"
    OVER_1_5 = "OVER_1_5"
