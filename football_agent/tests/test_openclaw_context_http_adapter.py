"""Unit tests for HttpOpenClawContextAdapter (mock HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from football_agent.openclaw_context.adapters.errors import (
    OpenClawContextConfigurationError,
    OpenClawContextError,
    OpenClawContextUnavailableError,
)
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.adapters.http_backend import HttpOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService


FIXTURES = Path(__file__).parent / "data"


def _mock_response(*, status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data
    return resp


def test_http_fetch_context_query_string() -> None:
    session = MagicMock()
    body = json.loads((FIXTURES / "openclaw_context_sample.json").read_text(encoding="utf-8"))
    session.get.return_value = _mock_response(json_data={"data": body})
    adapter = HttpOpenClawContextAdapter("http://openclaw.local", session=session)
    token = HttpOpenClawContextAdapter.build_query_token(
        home="AC Milan",
        away="Juventus",
        date="2025-11-29",
    )
    raw = adapter.fetch_context_raw(token)
    assert raw["query_home_team"] == "AC Milan"
    assert "home" in session.get.call_args[1]["params"]
    params = session.get.call_args[1]["params"]
    assert params["home"] == "AC Milan"
    assert params["away"] == "Juventus"


def test_http_fetch_context_json_token() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(json_data={"query_home_team": "A", "query_away_team": "B"})
    adapter = HttpOpenClawContextAdapter("http://openclaw.local", session=session)
    raw = adapter.fetch_context_raw('{"home":"A","away":"B","date":"2026-01-01"}')
    assert raw["query_home_team"] == "A"


def test_http_unavailable_on_network_error() -> None:
    session = MagicMock()
    session.get.side_effect = requests.Timeout("timeout")
    adapter = HttpOpenClawContextAdapter("http://openclaw.local", session=session)
    with pytest.raises(OpenClawContextUnavailableError, match="Request failed"):
        adapter.fetch_context_raw("home=A&away=B")


def test_http_configuration_error() -> None:
    adapter = HttpOpenClawContextAdapter("")
    with pytest.raises(OpenClawContextConfigurationError, match="URL is not set"):
        adapter.fetch_context_raw("home=A")


def test_same_interface_as_fixture_via_ingestion_service() -> None:
    fixture_ctx = OpenClawContextIngestionService(
        FixtureFileOpenClawContextAdapter(FIXTURES)
    ).get_context_for_fixture("openclaw_context_sample")

    body = json.loads((FIXTURES / "openclaw_context_sample.json").read_text(encoding="utf-8"))
    session = MagicMock()
    session.get.return_value = _mock_response(json_data=body)
    http_ctx = OpenClawContextIngestionService(
        HttpOpenClawContextAdapter("http://openclaw.local", session=session)
    ).get_context_for_fixture("home=AC Milan&away=Juventus")

    assert fixture_ctx is not None
    assert http_ctx is not None
    assert http_ctx.meta.query_home_team == fixture_ctx.meta.query_home_team
