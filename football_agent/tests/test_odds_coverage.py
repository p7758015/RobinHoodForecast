"""Phase Evaluation A — odds coverage model tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from football_agent.collectors.contracts import BLOCK_ODDS, BlockCollectionResult, MatchCollectionBundle, MatchRef
from football_agent.collectors.flashscore.odds_collector import FlashscoreOddsCollector
from football_agent.collectors.odds_bridge import collector_odds_to_context
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.odds.coverage import build_match_odds_coverage
from football_agent.odds.models import (
    MatchOddsContext,
    OddsMarketQuote,
    OddsMarketsBlock,
    OddsMeta,
    OddsProvenance,
)
from football_agent.odds.service import OddsIngestionService
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter

_FIXTURES = Path(__file__).resolve().parent / "data"


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="h4EoUB7T",
            source_url="https://example.invalid/mid=h4EoUB7T",
            home_team_name="Mexico",
            away_team_name="South Africa",
            competition_name="World Championship",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def _full_odds_context() -> MatchOddsContext:
    now = datetime.now(timezone.utc)
    return MatchOddsContext(
        meta=OddsMeta(
            fixture_id="h4EoUB7T",
            match_id="h4EoUB7T",
            source="flashscore_collector",
            home_team="Mexico",
            away_team="South Africa",
            collected_at_utc=now,
        ),
        markets=OddsMarketsBlock(
            home_win=OddsMarketQuote(odds_value=1.4, bookmaker_name="BetMGM.us"),
            away_win=OddsMarketQuote(odds_value=8.0, bookmaker_name="BetMGM.us"),
            double_chance_1x=OddsMarketQuote(
                odds_value=1.08,
                bookmaker_name="BetMGM.us",
                selection_name_raw="derived:double_chance_1x",
            ),
            double_chance_x2=OddsMarketQuote(odds_value=2.87, bookmaker_name="BetMGM.us"),
            btts_yes=OddsMarketQuote(odds_value=2.6, bookmaker_name="BetMGM.us"),
            over_1_5=OddsMarketQuote(odds_value=1.4, bookmaker_name="BetMGM.us"),
            under_3_5=OddsMarketQuote(odds_value=1.25, bookmaker_name="BetMGM.us"),
        ),
        provenance=OddsProvenance(
            backend_name="flashscore_collector",
            collected_at_utc=now,
            extraction_warnings=["collector_odds_derived:HOME_OR_DRAW"],
        ),
    )


def test_coverage_full_odds_match() -> None:
    cov = build_match_odds_coverage(_full_odds_context())
    assert cov.has_any_odds is True
    assert cov.odds_usable_for_parlay is True
    assert cov.has_1x2_odds is True
    assert cov.has_btts_odds is True
    assert cov.markets["home_win"].has_odds is True
    assert cov.markets["home_win"].suitable_for_pricing is True
    assert cov.markets["home_win"].pricing_quality == "book"
    assert cov.markets["double_chance_1x"].derived is True
    assert cov.markets["double_chance_1x"].suitable_for_pricing is True
    assert cov.markets["draw"].has_odds is False


def test_coverage_no_odds() -> None:
    cov = build_match_odds_coverage(None)
    assert cov.has_any_odds is False
    assert cov.odds_usable_for_parlay is False
    assert all(not e.has_odds for e in cov.markets.values())


def test_bridge_attaches_coverage() -> None:
    raw = json.loads((_FIXTURES / "scraper_odds_sample.json").read_text(encoding="utf-8"))
    bundle = MatchCollectionBundle(
        match_key="h4EoUB7T:Mexico:South Africa",
        match_ref=MatchRef(),
        blocks={
            BLOCK_ODDS: FlashscoreOddsCollector().collect(raw, MatchRef()),
        },
    )
    ctx = collector_odds_to_context(bundle, _facts())
    assert ctx is not None
    assert ctx.coverage is not None
    assert ctx.coverage.has_any_odds is True
    assert ctx.coverage.markets["home_win"].has_odds is True


def test_fixture_odds_coverage_via_ingestion() -> None:
    ctx = OddsIngestionService(FixtureFileOddsAdapter(_FIXTURES)).get_odds_for_fixture("odds_sample")
    assert ctx is not None
    cov = build_match_odds_coverage(ctx)
    assert cov.has_any_odds is True
    assert cov.markets["home_win"].has_odds is True
