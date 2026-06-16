"""Map scorer LeagueMarketKey values to odds coverage snake_case keys."""

from __future__ import annotations

from typing import Optional

SCORER_TO_COVERAGE: dict[str, str] = {
    "HOME_WIN": "home_win",
    "DRAW": "draw",
    "AWAY_WIN": "away_win",
    "HOME_NOT_LOSE": "double_chance_1x",
    "AWAY_NOT_LOSE": "double_chance_x2",
    "BTTS_YES": "btts_yes",
    "OVER_1_5": "over_1_5",
    "UNDER_3_5": "under_3_5",
    "HOME_TEAM_TO_SCORE": "home_team_to_score_yes",
    "AWAY_TEAM_TO_SCORE": "away_team_to_score_yes",
}


def scorer_market_to_coverage_key(market_key: str) -> Optional[str]:
    return SCORER_TO_COVERAGE.get(str(market_key or "").strip().upper())
