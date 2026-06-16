"""Collector odds bridge tests (Odds B)."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.odds_bridge import (
    SOURCE_FLASHSCORE_COLLECTOR,
    collector_odds_to_context,
    merge_collector_and_enrichment_odds,
    resolve_pipeline_odds_context,
)
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import MARKET_FIELDS, OddsIngestionService
from football_agent.services.scoring_service_v2 import ScoringServiceV2

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _facts():
    svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(_FIXTURES))
    return svc.get_facts_for_match("flashscore_sample_league_match")


def _enrichment_odds():
    return OddsIngestionService(FixtureFileOddsAdapter(_FIXTURES)).get_odds_for_fixture("odds_sample")


def _bundle_with_odds(markets: dict) -> tuple:
    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    raw = enrich_http_flashscore_raw(raw)
    raw["odds"] = {"bookmaker_name": "Flashscore", "markets": markets}
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    facts = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(_FIXTURES)).get_facts_for_match(
        "flashscore_sample_league_match",
    )
    return bundle, facts


def test_bridge_missing_block_returns_none() -> None:
    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    bundle, _ = MatchCollectorOrchestrator().collect_from_raw(enrich_http_flashscore_raw(raw), MatchRef())
    facts = _facts()
    assert facts is not None
    assert collector_odds_to_context(bundle, facts) is None


def test_bridge_maps_collector_markets_to_service_fields() -> None:
    bundle, facts = _bundle_with_odds(
        {
            "home_win": {"value": 1.85, "raw_label": "1"},
            "draw": {"value": 3.4, "raw_label": "X"},
            "away_win": {"value": 4.5, "raw_label": "2"},
            "double_chance_1x": {"value": 1.22, "raw_label": "1X"},
            "double_chance_x2": {"value": 1.95, "raw_label": "X2"},
            "btts_yes": {"value": 1.8, "raw_label": "Yes"},
            "over_1_5": {"value": 1.3, "raw_label": "O1.5"},
            "under_3_5": {"value": 1.55, "raw_label": "U3.5"},
        },
    )
    assert facts is not None
    ctx = collector_odds_to_context(bundle, facts)
    assert ctx is not None
    assert ctx.meta.source == SOURCE_FLASHSCORE_COLLECTOR
    mk = ctx.markets
    assert mk.home_win is not None and mk.home_win.odds_value == 1.85
    assert mk.away_win is not None
    assert mk.double_chance_1x is not None
    assert mk.double_chance_x2 is not None
    assert mk.btts_yes is not None
    assert mk.over_1_5 is not None
    assert mk.under_3_5 is not None
    assert "collector_odds_draw_not_in_v1_contract" in ctx.provenance.extraction_warnings
    assert "collector_odds_filled:" in ctx.provenance.extraction_warnings[-2]


def test_bridge_partial_preserves_warnings() -> None:
    bundle, facts = _bundle_with_odds({"home_win": {"value": 2.0, "raw_label": "1"}})
    assert facts is not None
    ctx = collector_odds_to_context(bundle, facts)
    assert ctx is not None
    assert ctx.markets.home_win is not None
    assert len(ctx.provenance.missing_markets) == len(MARKET_FIELDS) - 1


def test_merge_collector_primary_on_conflict() -> None:
    bundle, facts = _bundle_with_odds(
        {"home_win": {"value": 1.9, "raw_label": "1"}, "btts_yes": {"value": 1.7, "raw_label": "Yes"}},
    )
    enrichment = _enrichment_odds()
    assert facts is not None and enrichment is not None
    collector_ctx = collector_odds_to_context(bundle, facts)
    merged, warnings, source = merge_collector_and_enrichment_odds(collector_ctx, enrichment)
    assert source == "mixed"
    assert merged is not None
    assert merged.markets.home_win is not None
    assert merged.markets.home_win.odds_value == 1.9
    assert merged.markets.away_win is not None
    assert merged.markets.away_win.odds_value == enrichment.markets.away_win.odds_value
    assert any("odds_bridge_conflict:home_win" in w for w in warnings)


def test_merge_enrichment_only_when_collector_missing() -> None:
    enrichment = _enrichment_odds()
    merged, warnings, source = merge_collector_and_enrichment_odds(None, enrichment)
    assert source == "enrichment"
    assert merged is not enrichment
    assert merged.coverage is not None
    assert merged.coverage.has_any_odds is True
    assert not warnings


def test_scorer_receives_target_markets_after_bridge() -> None:
    bundle, facts = _bundle_with_odds(
        {
            "home_win": {"value": 1.85, "raw_label": "1"},
            "away_win": {"value": 4.5, "raw_label": "2"},
            "double_chance_1x": {"value": 1.22, "raw_label": "1X"},
            "btts_yes": {"value": 1.8, "raw_label": "Yes"},
            "over_1_5": {"value": 1.3, "raw_label": "O1.5"},
            "under_3_5": {"value": 1.55, "raw_label": "U3.5"},
        },
    )
    assert facts is not None
    ctx, warnings, source = resolve_pipeline_odds_context(
        facts=facts,
        collector_bundle=bundle,
        enrichment_odds=None,
    )
    assert source == "collector"
    assert ctx is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=ctx)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.odds.home_win is not None
    assert snap.odds.away_win is not None
    assert snap.odds.home_not_lose is not None
    assert snap.odds.btts_yes is not None
    assert snap.odds.over_15 is not None
    scored = ScoringServiceV2().score_snapshot_with_report(snap, report)
    assert scored is not None
