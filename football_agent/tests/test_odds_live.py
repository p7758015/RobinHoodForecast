"""Unit tests for live odds fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.odds.adapters.errors import OddsServiceUnavailableError
from football_agent.services.odds_live import fetch_odds_for_facts, resolve_odds_service_url


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="dC2J6FlK",
            source_url="https://flashscore.com/m/?mid=dC2J6FlK",
            competition_name="Botola Pro",
            home_team_name="Home",
            away_team_name="Away",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def test_resolve_odds_url_skip() -> None:
    assert resolve_odds_service_url("http://odds", skip=True) is None


@patch("football_agent.services.odds_live.OddsIngestionService")
@patch("football_agent.services.odds_live.HttpOddsAdapter")
def test_fetch_returns_failed_on_timeout(mock_adapter_cls, mock_svc_cls) -> None:
    mock_adapter_cls.return_value = MagicMock()
    mock_svc_cls.return_value.get_odds_for_fixture.side_effect = OddsServiceUnavailableError(
        "timeout",
    )

    ctx, sources, warnings = fetch_odds_for_facts(
        _facts(),
        odds_url="http://localhost:4000",
    )

    assert ctx is None
    assert sources["odds"] == "failed"
    assert warnings


def test_fetch_skipped_without_url() -> None:
    ctx, sources, warnings = fetch_odds_for_facts(_facts(), skip=True)
    assert ctx is None
    assert sources["odds"] == "skipped"
    assert warnings == []
