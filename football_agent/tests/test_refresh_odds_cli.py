"""CLI smoke for odds refresh job."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.jobs.refresh_odds import main
from football_agent.services.odds_refresh_service import OddsRefreshResult


@patch("football_agent.jobs.refresh_odds.config.USE_COLLECTOR_LAYER", True)
@patch("football_agent.jobs.refresh_odds.OddsRefreshService.refresh_for_match_url")
def test_cli_refresh_match_url(mock_refresh) -> None:
    mock_refresh.return_value = OddsRefreshResult(
        success=True,
        refreshed=True,
        match_key="k1",
        warnings=["odds_refresh_completed"],
    )
    code = main(["--match-url", "https://example.com/m", "--json"])
    assert code == 0
    mock_refresh.assert_called_once()


@patch("football_agent.jobs.refresh_odds.config.USE_COLLECTOR_LAYER", False)
def test_cli_requires_collector_layer() -> None:
    code = main(["--match-url", "https://example.com/m"])
    assert code == 2
