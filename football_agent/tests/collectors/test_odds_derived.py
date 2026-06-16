"""Derived odds tests (Odds C-lite)."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.flashscore.odds_collector import FlashscoreOddsCollector
from football_agent.collectors.odds_bridge import collector_odds_to_context
from football_agent.collectors.odds_derived import (
    apply_derived_double_chance_markets,
    derive_double_chance_price,
)
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _base_raw() -> dict:
    data = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_derive_double_chance_price_formula() -> None:
    # 1/1.85 + 1/3.4 ≈ 0.8347 → price ≈ 1.20
    price = derive_double_chance_price(1.85, 3.4)
    assert price is not None
    assert price == 1.2


def test_collector_creates_derived_1x_from_1x2_legs() -> None:
    raw = _base_raw()
    raw["odds"] = {"markets": {"1": 1.85, "X": 3.4, "2": 4.5}}
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    markets = result.payload["markets"]
    assert result.payload["market_count"] == 3
    assert result.payload["derived_market_count"] == 2
    assert "HOME_OR_DRAW" in markets
    assert markets["HOME_OR_DRAW"]["derived"] is True
    assert markets["HOME_OR_DRAW"]["derived_from"] == ["HOME_WIN", "DRAW"]
    assert markets["HOME_OR_DRAW"]["value"] == 1.2
    assert "AWAY_OR_DRAW" in markets
    assert markets["AWAY_OR_DRAW"]["value"] == 1.94
    assert "odds_derived_created:HOME_OR_DRAW" in result.warnings
    assert "odds_derived_created:AWAY_OR_DRAW" in result.warnings


def test_collector_skips_derived_when_real_double_chance_exists() -> None:
    raw = _base_raw()
    raw["odds"] = {
        "markets": {
            "home_win": 1.85,
            "draw": 3.4,
            "away_win": 4.5,
            "double_chance_1x": 1.22,
        },
    }
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    markets = result.payload["markets"]
    assert markets["HOME_OR_DRAW"]["value"] == 1.22
    assert "derived" not in markets["HOME_OR_DRAW"]
    assert not any("odds_derived_created:HOME_OR_DRAW" == w for w in result.warnings)


def test_derived_failed_on_invalid_source_values() -> None:
    markets, warnings = apply_derived_double_chance_markets(
        {
            "HOME_WIN": {"value": 1.0, "raw_label": "1"},
            "DRAW": {"value": 3.4, "raw_label": "X"},
        },
    )
    assert "HOME_OR_DRAW" not in markets
    assert "odds_derived_failed:HOME_OR_DRAW_invalid_source" in warnings


def test_bridge_maps_derived_to_double_chance_snapshot_fields() -> None:
    raw = _base_raw()
    raw["odds"] = {"markets": {"1": 2.0, "X": 3.2, "2": 3.8}}
    bundle, _ = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    facts = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(_FIXTURES)).get_facts_for_match(
        "flashscore_sample_league_match",
    )
    assert facts is not None
    ctx = collector_odds_to_context(bundle, facts)
    assert ctx is not None
    assert ctx.markets.double_chance_1x is not None
    assert ctx.markets.double_chance_x2 is not None
    assert "derived:1X" in (ctx.markets.double_chance_1x.selection_name_raw or "")
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=ctx)
    snap, _report = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.odds.home_not_lose is not None
    assert snap.odds.away_not_lose is not None
