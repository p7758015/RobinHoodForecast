"""Unit tests for HttpFlashscoreScraperAdapter (mock HTTP, no live scraper)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.service import FlashscoreIngestionService


def _mock_response(*, status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data
    return resp


def test_http_fetch_match_raw_unwraps_payload() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(
        json_data={"payload": {"match_id": "fs-99", "home_team_name": "A", "away_team_name": "B"}},
    )
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000", session=session)
    raw = adapter.fetch_match_raw("https://flashscore.com/match/abc")
    assert raw["match_id"] == "fs-99"
    assert raw["scraper_backend_name"] == "http"
    session.get.assert_called_once()
    call_kwargs = session.get.call_args
    assert call_kwargs[1]["params"]["url"].startswith("https://")


def test_http_fetch_matches_for_date_list() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(
        json_data={"matches": [{"match_id": "1", "home_team_name": "H", "away_team_name": "A"}]},
    )
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000", session=session)
    rows = adapter.fetch_matches_for_date("2026-06-10")
    assert len(rows) == 1
    assert rows[0]["match_id"] == "1"


def test_http_unavailable_on_connection_error() -> None:
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("refused")
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000", session=session)
    with pytest.raises(FlashscoreScraperUnavailableError, match="Request failed"):
        adapter.fetch_match_raw("fs-1")


def test_http_unavailable_on_http_503() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=503, text="down")
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000", session=session)
    with pytest.raises(FlashscoreScraperUnavailableError, match="HTTP 503"):
        adapter.fetch_match_raw("fs-1")


def test_http_configuration_error_empty_base_url() -> None:
    adapter = HttpFlashscoreScraperAdapter("")
    with pytest.raises(FlashscoreScraperConfigurationError, match="URL is not set"):
        adapter.fetch_match_raw("x")


def test_http_invalid_json_object_raises() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(json_data=[])
    adapter = HttpFlashscoreScraperAdapter("http://localhost:3000", session=session)
    with pytest.raises(FlashscoreScraperError, match="Expected JSON object"):
        adapter.fetch_match_raw("fs-1")


def test_same_interface_as_fixture_adapter_via_ingestion_service() -> None:
    """Both adapters feed FlashscoreIngestionService with compatible raw dicts."""
    from pathlib import Path

    fixtures = Path(__file__).parent / "data"
    fixture_raw = FixtureFileFlashscoreAdapter(fixtures).fetch_match_raw("flashscore_sample_league_match")
    http_session = MagicMock()
    http_session.get.return_value = _mock_response(json_data=fixture_raw)
    http_raw = HttpFlashscoreScraperAdapter("http://localhost:3000", session=http_session).fetch_match_raw("x")

    fs_fixture = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(fixtures)).get_facts_for_match(
        "flashscore_sample_league_match"
    )
    fs_http = FlashscoreIngestionService(
        HttpFlashscoreScraperAdapter("http://localhost:3000", session=http_session)
    ).get_facts_for_match("x")

    assert fs_fixture is not None
    assert fs_http is not None
    assert fs_http.meta.home_team_name == fs_fixture.meta.home_team_name
