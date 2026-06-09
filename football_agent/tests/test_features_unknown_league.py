"""Motivation fail-soft for unknown competition total_rounds."""

from __future__ import annotations

from football_agent.domain.features import calculate_motivation
from football_agent.domain.models import StandingEntry, Team
from football_agent.league_registry import UNKNOWN_TOTAL_ROUNDS_WARNING


def _standings(n: int = 20) -> list[StandingEntry]:
    return [
        StandingEntry(
            position=i,
            team=Team(id=i, name=f"T{i}", short_name=f"T{i}"),
            played_games=10,
            points=30 - i,
            won=0,
            draw=0,
            lost=0,
            goals_for=0,
            goals_against=0,
            goal_difference=0,
            form="",
        )
        for i in range(1, n + 1)
    ]


def test_calculate_motivation_unknown_total_rounds_warning() -> None:
    m, eliminated, fighting, warnings = calculate_motivation(
        position=10,
        points=25,
        played_rounds=10,
        total_rounds=None,
        standings=_standings(),
        competition_code="UNKNOWN_LEAGUE",
    )
    assert UNKNOWN_TOTAL_ROUNDS_WARNING in warnings
    assert 0.0 <= m <= 1.0
    assert eliminated is False
    assert fighting in (True, False)


def test_calculate_motivation_known_league_no_warning() -> None:
    _, _, _, warnings = calculate_motivation(
        position=5,
        points=40,
        played_rounds=20,
        total_rounds=38,
        standings=_standings(),
        competition_code="PL",
    )
    assert UNKNOWN_TOTAL_ROUNDS_WARNING not in warnings
