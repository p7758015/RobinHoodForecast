"""Pipeline integration for collector odds bridge (Odds B)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from football_agent.collectors.contracts import MatchCollectionBundle
from football_agent.collectors.odds_bridge import SOURCE_FLASHSCORE_COLLECTOR
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.services.enrichment_contract import SOURCE_SKIPPED, SOURCE_SKIPPED_NOT_CONFIGURED
from football_agent.services.enrichment_live import EnrichmentFetchResult
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline

_FIXTURES = Path(__file__).parent / "data"


def _facts():
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(_FIXTURES),
    ).get_facts_for_match("flashscore_sample_league_match")
    assert facts is not None
    return facts


def _bundle_with_odds() -> MatchCollectionBundle:
    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    raw = enrich_http_flashscore_raw(raw)
    raw["odds"] = {
        "markets": {
            "home_win": {"value": 1.9, "raw_label": "1"},
            "away_win": {"value": 3.8, "raw_label": "2"},
            "btts_yes": {"value": 1.7, "raw_label": "Yes"},
            "over_1_5": {"value": 1.25, "raw_label": "O1.5"},
        },
    }
    from football_agent.collectors.contracts import MatchRef
    from football_agent.collectors.orchestrator import MatchCollectorOrchestrator

    bundle, _ = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    return bundle


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", True)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts_collector")
def test_pipeline_uses_collector_odds_when_enrichment_skipped(mock_collector, mock_enrich, _url) -> None:
    facts = _facts()
    bundle = _bundle_with_odds()
    mock_collector.return_value = (facts, {"flashscore": "ok", "collector": "ok"}, None, [], bundle)
    mock_enrich.return_value = EnrichmentFetchResult(
        context=None,
        odds=None,
        sources={"openclaw": SOURCE_SKIPPED_NOT_CONFIGURED, "odds": SOURCE_SKIPPED},
        warnings=[],
    )

    result = LiveFlashscorePipeline(persist=False, skip_odds=True).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources.get("odds_bridge") == "collector"
    assert result.sources.get("odds") in ("ok", "partial")
    assert any("collector_odds" in w or "odds_bridge" in w for w in result.warnings)
    assert result.scored_run is not None
    assert result.scored_run.snapshot.odds.home_win is not None
    assert result.scored_run.snapshot.odds.home_win.source == SOURCE_FLASHSCORE_COLLECTOR


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", False)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
def test_legacy_path_unchanged_without_collector_layer(mock_fs, mock_enrich, _url) -> None:
    facts = _facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = EnrichmentFetchResult(
        context=None,
        odds=None,
        sources={"openclaw": SOURCE_SKIPPED_NOT_CONFIGURED, "odds": SOURCE_SKIPPED},
        warnings=[],
    )

    result = LiveFlashscorePipeline(persist=False, skip_odds=True).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert "odds_bridge" not in result.sources
    assert result.scored_run.snapshot.odds.home_win is None


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", True)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts_collector")
def test_pipeline_succeeds_when_collector_odds_missing(mock_collector, mock_enrich, _url) -> None:
    facts = _facts()
    from football_agent.collectors.contracts import MatchRef
    from football_agent.collectors.orchestrator import MatchCollectorOrchestrator

    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    bundle, _ = MatchCollectorOrchestrator().collect_from_raw(enrich_http_flashscore_raw(raw), MatchRef())
    mock_collector.return_value = (facts, {"flashscore": "ok"}, None, ["collector_odds_missing"], bundle)
    mock_enrich.return_value = EnrichmentFetchResult(
        context=None,
        odds=None,
        sources={"odds": SOURCE_SKIPPED},
        warnings=[],
    )

    result = LiveFlashscorePipeline(persist=False, skip_odds=True).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources.get("odds_bridge") is None or result.sources.get("odds_bridge") == "none"
