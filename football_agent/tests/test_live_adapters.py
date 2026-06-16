"""Stage 4 live adapter tests (mock HTTP only, no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.openclaw_context.adapters.errors import OpenClawContextUnavailableError
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.services.enrichment_contract import SOURCE_FAILED, SOURCE_SKIPPED_NOT_CONFIGURED
from football_agent.services.enrichment_live import fetch_enrichment_for_facts
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline
from football_agent.services.odds_live import fetch_odds_for_facts
from football_agent.services.openclaw_context_live import fetch_openclaw_context_for_facts


def _facts() -> FlashscoreMatchFacts:
    """Valid matchmeta for collector validation (teams + competition)."""
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="abc123",
            source_url="https://www.flashscore.com/match/?mid=abc123",
            competition_name="Botola Pro",
            home_team_name="Kawkab Marrakech",
            away_team_name="Raja Casablanca",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="http"),
    )


def test_openclaw_live_fetch_fail_soft() -> None:
    with patch(
        "football_agent.services.openclaw_context_live.OpenClawContextIngestionService",
    ) as mock_svc:
        mock_svc.return_value.get_context_for_fixture.side_effect = OpenClawContextUnavailableError("down")
        ctx, sources, warnings = fetch_openclaw_context_for_facts(
            _facts(),
            openclaw_url="http://openclaw.local",
        )
    assert ctx is None
    assert sources["openclaw"] == "failed"
    assert any("openclaw_context_fetch_failed" in w for w in warnings)


def test_odds_live_skipped_when_not_configured() -> None:
    with patch("football_agent.services.odds_live.resolve_odds_service_url", return_value=None):
        ctx, sources, warnings = fetch_odds_for_facts(_facts())
    assert ctx is None
    assert sources["odds"] == "skipped"
    assert not warnings


def test_enrichment_live_never_raises_on_http_failure() -> None:
    with patch("football_agent.services.enrichment_live._fetch_context_split") as mock_ctx:
        mock_ctx.return_value = (None, SOURCE_FAILED, ["openclaw_context_fetch_failed:timeout"])
        with patch("football_agent.services.enrichment_live._fetch_odds_split") as mock_odds:
            mock_odds.return_value = (None, SOURCE_SKIPPED_NOT_CONFIGURED, [])
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
                with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
                    result = fetch_enrichment_for_facts(_facts())
    assert result.context is None
    assert result.sources["openclaw"] == SOURCE_FAILED
    assert any("openclaw_context_fetch_failed" in w for w in result.warnings)


def test_http_flashscore_adapter_match_id_param() -> None:
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000")
    captured: dict = {}

    def _capture_get_json(_session, _url, *, params, **kwargs):
        captured["params"] = params
        return {"match_id": "x"}

    with patch("football_agent.flashscore.adapters.http_backend.get_json", side_effect=_capture_get_json):
        with patch.object(adapter, "_normalize_single_record", return_value={"match_id": "x"}):
            adapter.fetch_match_raw("Sfgk1gCs")
    assert captured.get("params") == {"match_id": "Sfgk1gCs"}


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_flashscore_only_openclaw_not_configured(mock_enrich, mock_fs, _url) -> None:
    facts = _facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    from football_agent.services.enrichment_live import EnrichmentFetchResult

    mock_enrich.return_value = EnrichmentFetchResult(
        sources={"openclaw": SOURCE_SKIPPED_NOT_CONFIGURED, "odds": SOURCE_SKIPPED_NOT_CONFIGURED},
        warnings=["enrichment_not_configured"],
    )

    result = LiveFlashscorePipeline(persist=False, skip_openclaw=False).analyze_flashscore_url(
        "https://example.com/m",
    )

    assert result.success is True
    assert result.sources["flashscore"] == "ok"
    assert result.sources["openclaw"] == SOURCE_SKIPPED_NOT_CONFIGURED
    assert "openclaw_context" in (result.scored_run.build_report.merge_missing_blocks if result.scored_run else [])


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value=None)
def test_pipeline_missing_scraper_url_fail_soft(_url) -> None:
    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")
    assert result.success is False
    assert result.stage_failed == "config"
