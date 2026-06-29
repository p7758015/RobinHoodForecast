"""Unified enrichment fetcher tests (mock only, no network)."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.services.enrichment_contract import (
    SOURCE_FAILED,
    SOURCE_SKIPPED_NOT_CONFIGURED,
    parse_unified_enrichment_payload,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult, fetch_enrichment_for_facts


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="m1",
            source_url="https://example.com",
            competition_name="Test",
            home_team_name="Home",
            away_team_name="Away",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def _patch_enrichment_not_configured():
    """Context manager stack: no OpenClaw URLs, primary off, USE_OPENCLAW off."""
    return (
        patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None),
        patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", None),
        patch("football_agent.services.enrichment_config.config.OPENCLAW_CONTEXT_BASE_URL", None),
        patch("football_agent.services.enrichment_config.config.OPENCLAW_GATEWAY_URL", None),
        patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None),
        patch("football_agent.services.enrichment_config.config.OPENCLAW_PRIMARY_ENRICHMENT", False),
        patch("football_agent.services.enrichment_config.config.USE_OPENCLAW", False),
    )


def test_fetch_not_configured_returns_skipped_statuses() -> None:
    with patch("football_agent.services.openclaw_news_enrichment.brave_news_enabled", return_value=False):
        patches = _patch_enrichment_not_configured()
        for p in patches:
            p.start()
        try:
            result = fetch_enrichment_for_facts(_facts())
        finally:
            for p in patches:
                p.stop()
    assert result.context is None
    assert result.odds is None
    assert result.sources["openclaw"] == SOURCE_SKIPPED_NOT_CONFIGURED
    assert result.sources["odds"] == SOURCE_SKIPPED_NOT_CONFIGURED
    assert "enrichment_not_configured" in result.warnings


def test_parse_unified_payload_splits_blocks() -> None:
    ctx_raw, odds_raw, warnings = parse_unified_enrichment_payload(
        {
            "context": {"squad_context": {"home": {}, "away": {}}},
            "odds": {"markets": {"home_win": 2.1}},
        },
    )
    assert ctx_raw is not None
    assert odds_raw is not None
    assert not warnings


def test_parse_unified_empty_payload_warns() -> None:
    _, _, warnings = parse_unified_enrichment_payload({})
    assert "enrichment_unified_empty" in warnings


@patch("football_agent.services.enrichment_live._fetch_context_split")
@patch("football_agent.services.enrichment_live._fetch_odds_split")
def test_fetch_split_openclaw_context_without_odds(mock_odds, mock_ctx) -> None:
    from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
    from football_agent.openclaw_context.service import OpenClawContextIngestionService
    from pathlib import Path

    fixtures = Path(__file__).parent / "data"
    ctx = OpenClawContextIngestionService(
        FixtureFileOpenClawContextAdapter(fixtures),
    ).get_context_for_fixture("openclaw_context_sample")
    mock_ctx.return_value = (ctx, "ok", [])
    mock_odds.return_value = (None, SOURCE_FAILED, ["odds_empty_response"])

    with patch("football_agent.services.enrichment_config.probe_url_health", return_value=True):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
                with patch("football_agent.services.enrichment_config.config.OPENCLAW_GATEWAY_URL", None):
                    with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None):
                        result = fetch_enrichment_for_facts(_facts())

    assert result.context is not None
    assert result.odds is None
    assert result.sources["openclaw"] == "ok"
    assert result.sources["odds"] == SOURCE_FAILED
    assert any("enrichment_partial:context_without_odds" in w for w in result.warnings)


def test_enrichment_result_properties() -> None:
    result = EnrichmentFetchResult(
        sources={"enrichment_backend": "openclaw"},
        routing=None,
    )
    assert result.enrichment_mode == "not_configured"


def test_fetch_brave_only_without_openclaw_or_odds() -> None:
    from football_agent.news_context.models import MatchNewsContext
    from datetime import datetime, timezone

    news_ctx = MatchNewsContext(
        match_id="m1",
        home_team="Home",
        away_team="Away",
        source_count=2,
        confidence=0.6,
        collected_at_utc=datetime.now(timezone.utc),
    )

    with patch("football_agent.services.openclaw_news_enrichment.brave_news_enabled", return_value=True):
        with patch(
            "football_agent.services.openclaw_news_enrichment.enrich_match_news_from_brave",
            return_value=news_ctx,
        ) as mock_brave:
            patches = _patch_enrichment_not_configured()
            for p in patches:
                p.start()
            try:
                result = fetch_enrichment_for_facts(_facts())
            finally:
                for p in patches:
                    p.stop()

    assert result.context is None
    assert result.odds is None
    assert result.news is not None
    assert result.news.source_count == 2
    assert result.sources["openclaw"] == SOURCE_SKIPPED_NOT_CONFIGURED
    assert result.sources["odds"] == SOURCE_SKIPPED_NOT_CONFIGURED
    assert result.sources["brave_news"] == "ok"
    assert result.sources["enrichment_backend"] == "brave"
    assert "enrichment_not_configured" not in result.warnings
    mock_brave.assert_called_once()


@patch("football_agent.services.openclaw_news_enrichment.brave_news_enabled", return_value=False)
def test_fetch_brave_disabled_no_exception(_mock_enabled) -> None:
    patches = _patch_enrichment_not_configured()
    for p in patches:
        p.start()
    try:
        result = fetch_enrichment_for_facts(_facts())
    finally:
        for p in patches:
            p.stop()

    assert result.news is None
    assert "enrichment_not_configured" in result.warnings
