"""Unit tests for HttpOddsAdapter (mock HTTP, no live service)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from football_agent.odds.adapters.errors import (
    OddsServiceConfigurationError,
    OddsServiceError,
    OddsServiceUnavailableError,
)
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.adapters.http_backend import HttpOddsAdapter
from football_agent.odds.service import OddsIngestionService
from pathlib import Path


def _mock_response(*, status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data
    return resp


def test_http_fetch_odds_unwraps_payload() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(
        json_data={
            "payload": {
                "fixture_id": "live-1",
                "home_team": "Home",
                "away_team": "Away",
                "markets": {"home_win": 2.1},
            },
        },
    )
    adapter = HttpOddsAdapter("http://localhost:4000", session=session)
    raw = adapter.fetch_odds_raw("home=Home&away=Away&date=2026-06-09")
    assert raw["fixture_id"] == "live-1"
    assert raw["backend_name"] == "http"


def test_http_build_query_token_includes_match_id() -> None:
    token = HttpOddsAdapter.build_query_token(
        home="A",
        away="B",
        date="2026-06-09",
        match_id="dC2J6FlK",
    )
    assert "match_id=dC2J6FlK" in token


def test_http_unavailable_on_connection_error() -> None:
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("refused")
    adapter = HttpOddsAdapter("http://localhost:4000", session=session)
    with pytest.raises(OddsServiceUnavailableError, match="Request failed"):
        adapter.fetch_odds_raw("home=A&away=B")


def test_http_configuration_error_empty_base_url() -> None:
    adapter = HttpOddsAdapter("")
    with pytest.raises(OddsServiceConfigurationError, match="URL is not set"):
        adapter.fetch_odds_raw("x")


def test_http_same_interface_as_fixture_via_ingestion_service() -> None:
    fixtures = Path(__file__).parent / "data"
    fixture_raw = FixtureFileOddsAdapter(fixtures).fetch_odds_raw("odds_botola_sample_match")
    session = MagicMock()
    session.get.return_value = _mock_response(json_data=fixture_raw)
    http_raw = HttpOddsAdapter("http://localhost:4000", session=session).fetch_odds_raw("x")

    ctx_fixture = OddsIngestionService(FixtureFileOddsAdapter(fixtures)).get_odds_for_fixture(
        "odds_botola_sample_match",
    )
    ctx_http = OddsIngestionService(
        HttpOddsAdapter("http://localhost:4000", session=session),
    ).get_odds_for_fixture("x")

    assert ctx_fixture is not None
    assert ctx_http is not None
    assert ctx_http.meta.home_team == ctx_fixture.meta.home_team
