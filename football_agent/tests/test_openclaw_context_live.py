"""Unit tests for live OpenClaw context fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.openclaw_context.adapters.errors import OpenClawContextUnavailableError
from football_agent.services.openclaw_context_live import (
    fetch_openclaw_context_for_facts,
    resolve_openclaw_context_url,
)


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="x1",
            source_url="https://example.com",
            competition_name="Test",
            home_team_name="Home",
            away_team_name="Away",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def test_resolve_openclaw_url_skip() -> None:
    assert resolve_openclaw_context_url("http://oc", skip=True) is None


@patch("football_agent.services.openclaw_context_live.OpenClawContextIngestionService")
@patch("football_agent.services.openclaw_context_live.HttpOpenClawContextAdapter")
def test_fetch_returns_failed_on_timeout(mock_adapter_cls, mock_svc_cls) -> None:
    mock_adapter_cls.return_value = MagicMock()
    mock_svc_cls.return_value.get_context_for_fixture.side_effect = OpenClawContextUnavailableError(
        "timeout",
    )

    ctx, sources, warnings = fetch_openclaw_context_for_facts(
        _facts(),
        openclaw_url="http://localhost:9000",
    )

    assert ctx is None
    assert sources["openclaw"] == "failed"
    assert warnings


def test_fetch_skipped_without_url() -> None:
    ctx, sources, warnings = fetch_openclaw_context_for_facts(_facts(), skip=True)
    assert ctx is None
    assert sources["openclaw"] == "skipped"
    assert warnings == []
