"""Small helpers for API-Football odds (season inference, merge, diagnostics)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union

from football_agent.domain.models import Odds

ODDS_FIELD_NAMES = (
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


def infer_api_football_season(match_date: Union[date, datetime]) -> int:
    """API-Football season year = start of Aug–Jul cycle (e.g. May 2026 → 2025)."""
    if isinstance(match_date, datetime):
        match_date = match_date.date()
    return match_date.year if match_date.month >= 7 else match_date.year - 1


def seasons_to_try(*candidates: int) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for s in candidates:
        if s is None or s in seen:
            continue
        seen.add(s)
        out.append(int(s))
    return out


def count_odds_fields(odds: Optional[Odds]) -> int:
    if not odds:
        return 0
    return sum(1 for name in ODDS_FIELD_NAMES if getattr(odds, name, None) is not None)


def merge_odds(base: Odds, extra: Odds) -> Odds:
    """Fill missing fields on base from extra (first non-null wins per field)."""
    for name in ODDS_FIELD_NAMES:
        if getattr(base, name, None) is None:
            val = getattr(extra, name, None)
            if val is not None:
                setattr(base, name, val)
    if base.fixture_id == 0 and extra.fixture_id:
        base.fixture_id = extra.fixture_id
    return base


def odds_to_debug_dict(odds: Optional[Odds]) -> Dict[str, Any]:
    if not odds:
        return {"fixture_id": None, "markets": {}}
    markets = {name: getattr(odds, name, None) for name in ODDS_FIELD_NAMES}
    return {
        "fixture_id": odds.fixture_id,
        "filled_count": count_odds_fields(odds),
        "markets": markets,
    }
