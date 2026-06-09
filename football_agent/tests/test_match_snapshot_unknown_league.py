"""MatchSnapshotBuilder: unknown total_rounds → UNKNOWN phase + motivation warning."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from football_agent.domain.enums_v2 import SeasonPhase
from football_agent.domain.models import Match, StandingEntry, Team
from football_agent.league_registry import UNKNOWN_TOTAL_ROUNDS_WARNING
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder

UTC = timezone.utc


def _standing(team_id: int, pos: int) -> StandingEntry:
    return StandingEntry(
        position=pos,
        team=Team(id=team_id, name=f"Team{team_id}", short_name=f"T{team_id}"),
        played_games=12,
        points=20,
        won=5,
        draw=2,
        lost=5,
        goals_for=15,
        goals_against=14,
        goal_difference=1,
        form="WDL",
    )


def test_unknown_league_season_phase_and_motivation_warning() -> None:
    fd = MagicMock()
    af = MagicMock()
    fd.get_standings.return_value = [_standing(1, 1), _standing(2, 2)]
    fd.get_team_matches_season.return_value = []
    fd.get_team_coach.return_value = (None, None, None)
    fd.get_coach_matches.return_value = []
    fd.get_h2h_matches.return_value = []

    match = Match(
        id=99,
        competition_code="XYZ",
        home_team=Team(id=1, name="Home", short_name="H"),
        away_team=Team(id=2, name="Away", short_name="A"),
        utc_date=datetime(2024, 4, 25, 15, 0, tzinfo=UTC),
        status="SCHEDULED",
        matchday=12,
    )

    snap = MatchSnapshotBuilder(fd, af).build_snapshot_for_match(match)

    assert snap.match_meta.season_phase == SeasonPhase.UNKNOWN
    assert snap.match_meta.rounds_remaining is None
    assert UNKNOWN_TOTAL_ROUNDS_WARNING in snap.home_team_context.motivation.derivation_warnings
    assert UNKNOWN_TOTAL_ROUNDS_WARNING in snap.away_team_context.motivation.derivation_warnings
