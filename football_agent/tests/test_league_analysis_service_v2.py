"""Unit tests for LeagueAnalysisServiceV2 — mocked clients, no API."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from football_agent.domain.models import Match, Team
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.league_analysis_service_v2 import LeagueAnalysisServiceV2
from football_agent.tests.test_scorer_v2 import make_snapshot

UTC = timezone.utc


def _match(mid: int, code: str = "PL") -> Match:
    home = Team(id=1, name="Home FC", short_name="Home")
    away = Team(id=2, name="Away FC", short_name="Away")
    return Match(
        id=mid,
        competition_code=code,
        home_team=home,
        away_team=away,
        utc_date=datetime(2024, 4, 25, 15, mid % 60, tzinfo=UTC),
        status="SCHEDULED",
        matchday=34,
    )


def test_analyze_matches_for_date_fail_soft_per_match() -> None:
    fd = MagicMock()
    fd.get_matches_by_date.return_value = [_match(1), _match(2)]
    af = MagicMock()

    builder = MagicMock()
    builder.build_snapshot_for_match.side_effect = [
        make_snapshot(),
        RuntimeError("builder failed"),
    ]
    scorer = MagicMock()
    scorer.score_snapshot.return_value = LeagueScorerV2().score_snapshot(make_snapshot())

    service = LeagueAnalysisServiceV2(fd, af, snapshot_builder=builder, scorer=scorer)
    results = service.analyze_matches_for_date("2024-04-25", competition_code="PL")

    assert len(results) == 1
    fd.get_matches_by_date.assert_called_once_with("2024-04-25")
    assert builder.build_snapshot_for_match.call_count == 2
    assert scorer.score_snapshot.call_count == 1


def test_competition_filter() -> None:
    fd = MagicMock()
    fd.get_matches_by_date.return_value = [_match(1, "PL"), _match(2, "SA")]
    af = MagicMock()
    builder = MagicMock()
    builder.build_snapshot_for_match.return_value = make_snapshot()
    scorer = MagicMock()
    scorer.score_snapshot.return_value = LeagueScorerV2().score_snapshot(make_snapshot())

    service = LeagueAnalysisServiceV2(fd, af, snapshot_builder=builder, scorer=scorer)
    results = service.analyze_matches_for_date("2024-04-25", competition_code="pl")

    assert len(results) == 1
    assert builder.build_snapshot_for_match.call_count == 1
