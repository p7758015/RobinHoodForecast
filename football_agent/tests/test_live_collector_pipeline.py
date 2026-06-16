"""Live pipeline + collector-layer integration (mocked ingest, no HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline
from football_agent.tests.helpers.live_pipeline_mocks import collector_aborted_return, collector_ingest_return
from football_agent.tests.test_live_flashscore_pipeline_openclaw import _botola_facts, _enrichment_result

FIXTURES = Path(__file__).parent / "data"


def test_botola_fixture_passes_collector_match_meta() -> None:
    raw = json.loads((FIXTURES / "flashscore_botola_sample_match.json").read_text(encoding="utf-8"))
    enriched = enrich_http_flashscore_raw(raw)
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(
        enriched,
        MatchRef(match_url=enriched.get("source_url")),
    )
    assert bundle.aborted is False
    assert bundle.blocks["match_meta"].status != "failed"


def test_invalid_metadata_aborts_collector_orchestrator() -> None:
    raw = {
        "match_id": "bad1",
        "home_team_name": "Unknown",
        "away_team_name": "Away FC",
        "competition_name": "Botola Pro",
    }
    bundle, _ = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert bundle.aborted is True
    assert bundle.abort_reason == "match_meta_failed"


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", True)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts_collector")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_collector_on_valid_ingest_success(mock_enrich, mock_collector, _url) -> None:
    facts = _botola_facts()
    mock_collector.return_value = collector_ingest_return(facts)
    mock_enrich.return_value = _enrichment_result(openclaw="skipped", odds_status="skipped")

    result = LiveFlashscorePipeline(persist=False, skip_openclaw=True).analyze_flashscore_url(
        "https://example.com/m",
    )

    assert result.success is True
    assert result.sources.get("collector") == "ok"
    assert "brave_news" not in result.sources or result.sources.get("brave_news") != "failed"


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", True)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts_collector")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_collector_invalid_metadata_aborts(mock_enrich, mock_collector, _url) -> None:
    mock_collector.return_value = collector_aborted_return()
    mock_enrich.return_value = _enrichment_result(openclaw="skipped", odds_status="skipped")

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is False
    assert result.stage_failed == "flashscore_ingest"
    mock_enrich.assert_not_called()
