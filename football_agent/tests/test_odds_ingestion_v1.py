from __future__ import annotations

from pathlib import Path

from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.models import MatchOddsContext
from football_agent.odds.service import OddsIngestionService


def test_odds_ingestion_from_fixture_and_missing_markets() -> None:
    fixtures_dir = Path(__file__).parent / "data"
    svc = OddsIngestionService(FixtureFileOddsAdapter(fixtures_dir))

    ctx = svc.get_odds_for_fixture("odds_sample")
    assert isinstance(ctx, MatchOddsContext)

    assert ctx.meta.odds_format == "DECIMAL"
    assert ctx.meta.home_team == "AC Milan"
    assert ctx.meta.away_team == "Juventus"

    assert ctx.markets.home_win is not None
    assert ctx.markets.home_win.odds_value == 2.15
    assert ctx.markets.away_win is not None

    # fixture intentionally does not include under_3_5
    assert ctx.markets.under_3_5 is None
    assert "under_3_5" in ctx.provenance.missing_markets


def test_odds_ingestion_returns_none_when_fixture_missing() -> None:
    fixtures_dir = Path(__file__).parent / "data"
    svc = OddsIngestionService(FixtureFileOddsAdapter(fixtures_dir))
    assert svc.get_odds_for_fixture("does_not_exist") is None

