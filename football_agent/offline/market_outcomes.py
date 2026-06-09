"""Settle v2 market keys against final scores (offline calibration only)."""

from __future__ import annotations

from typing import Optional

V2_MARKET_KEYS = (
    "HOME_WIN",
    "AWAY_WIN",
    "HOME_NOT_LOSE",
    "AWAY_NOT_LOSE",
    "BTTS_YES",
    "HOME_TEAM_TO_SCORE",
    "AWAY_TEAM_TO_SCORE",
    "OVER_1_5",
)


def v2_market_is_win(market_key: str, home_score: int, away_score: int) -> Optional[bool]:
    """
    Return True/False for known markets, None if market_key is unknown.
    OVER_1_5: total goals > 1.5 (i.e. >= 2).
    """
    total = home_score + away_score
    if market_key == "HOME_WIN":
        return home_score > away_score
    if market_key == "AWAY_WIN":
        return away_score > home_score
    if market_key == "HOME_NOT_LOSE":
        return home_score >= away_score
    if market_key == "AWAY_NOT_LOSE":
        return away_score >= home_score
    if market_key == "BTTS_YES":
        return home_score >= 1 and away_score >= 1
    if market_key == "HOME_TEAM_TO_SCORE":
        return home_score >= 1
    if market_key == "AWAY_TEAM_TO_SCORE":
        return away_score >= 1
    if market_key == "OVER_1_5":
        return total >= 2
    return None
