"""OpenClaw bridge service tests (no external network)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import requests

from football_agent.openclaw_bridge.backend_client import LiveBackendResult, NoopLiveBackend
from football_agent.openclaw_bridge.enricher import BridgeEnricher
from football_agent.openclaw_bridge.models import BridgeMatchInput, BridgeMode
from football_agent.openclaw_bridge.server import make_server
from football_agent.openclaw_context.adapters.http_backend import HttpOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.odds.adapters.http_backend import HttpOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.services.enrichment_config import (
    enrichment_uses_bridge,
    resolve_openclaw_base_url,
)

_FIXTURES = Path(__file__).parent / "data"


def _match_input() -> BridgeMatchInput:
    return BridgeMatchInput(
        home_team="Avai",
        away_team="Ceara",
        competition_name="Brazil Serie B",
        date="2026-06-10",
        match_id="6FiXiHcc",
    )


def _load_fixture(name: str) -> Dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _MockLiveBackend:
    def __init__(self, context: Dict[str, Any] | None = None, odds: Dict[str, Any] | None = None) -> None:
        self._context = context
        self._odds = odds

    def fetch_context_blocks(self, inp: BridgeMatchInput) -> LiveBackendResult:
        if self._context is None:
            return LiveBackendResult(warnings=["backend_unavailable"], source="mock")
        return LiveBackendResult(data=self._context, warnings=[], source="mock")

    def fetch_odds_markets(self, inp: BridgeMatchInput) -> LiveBackendResult:
        if self._odds is None:
            return LiveBackendResult(warnings=["backend_unavailable"], source="mock")
        return LiveBackendResult(data=self._odds, warnings=[], source="mock")


def test_bridge_health_endpoint() -> None:
    server = make_server("127.0.0.1", 0, enricher_factory=lambda: BridgeEnricher(mode=BridgeMode.PROTOTYPE))
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        resp = requests.get(f"http://{host}:{port}/health", timeout=3)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["service"] == "openclaw_bridge"
    finally:
        server.shutdown()


def test_bridge_context_contract_maps_to_openclaw_service() -> None:
    server = make_server("127.0.0.1", 0, enricher_factory=lambda: BridgeEnricher(mode=BridgeMode.PROTOTYPE))
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{host}:{port}"
        token = HttpOpenClawContextAdapter.build_query_token(
            home="Avai",
            away="Ceara",
            competition_name="Brazil Serie B",
            date="2026-06-10",
        )
        adapter = HttpOpenClawContextAdapter(base, timeout_s=5.0)
        raw = adapter.fetch_context_raw(token)
        ctx = OpenClawContextIngestionService(adapter).get_context_for_fixture(token)
        assert ctx is not None
        assert ctx.meta.query_home_team == "Avai"
        assert ctx.provenance.backend_name == "openclaw_bridge"
        assert ctx.motivation_narrative is not None
        assert "bridge_prototype_mode" in (raw.get("extraction_warnings") or [])
    finally:
        server.shutdown()


def test_bridge_odds_endpoint_returns_markets() -> None:
    server = make_server("127.0.0.1", 0, enricher_factory=lambda: BridgeEnricher(mode=BridgeMode.PROTOTYPE))
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{host}:{port}"
        token = HttpOddsAdapter.build_query_token(home="Avai", away="Ceara", date="2026-06-10")
        ctx = OddsIngestionService(HttpOddsAdapter(base, timeout_s=5.0)).get_odds_for_fixture(token)
        assert ctx is not None
        assert ctx.markets.double_chance_1x is not None
        assert ctx.provenance.backend_name == "openclaw_bridge"
    finally:
        server.shutdown()


def test_live_assisted_detects_html_gateway() -> None:
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=NoopLiveBackend(),
    )
    mock_resp = MagicMock()
    mock_resp.text = "<html><title>OpenClaw Control</title></html>"
    mock_resp.json.side_effect = ValueError("not json")

    with patch("football_agent.openclaw_bridge.enricher.requests.get", return_value=mock_resp):
        envelope = enricher.enrich_context(_match_input())

    assert "openclaw_gateway_returns_html_not_json" in envelope.warnings
    assert "bridge_prototype_fallback" in envelope.warnings
    assert envelope.payload.get("motivation_narrative") is not None
    assert envelope.completeness > 0
    assert "bridge_prototype_mode" not in envelope.warnings


def test_live_assisted_gateway_unreachable_fail_soft() -> None:
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=NoopLiveBackend(),
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("down"),
    ):
        envelope = enricher.enrich_context(_match_input())

    assert any("openclaw_gateway_unreachable" in w for w in envelope.warnings)
    assert "backend_unavailable" in envelope.warnings
    assert "bridge_prototype_fallback" in envelope.warnings
    assert envelope.payload.get("query_home_team") == "Avai"


def test_live_assisted_successful_backend_context() -> None:
    live_context = _load_fixture("openclaw_bridge_live_context.json")
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=_MockLiveBackend(context=live_context, odds=None),
        disable_live_backend=True,
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("skip probe"),
    ):
        envelope = enricher.enrich_context(_match_input())

    assert "live_backend_context_ok" in envelope.warnings
    assert "bridge_prototype_mode" not in envelope.warnings
    assert envelope.payload["coach_context"]["home"]["coach_name"] == "Coach Avai"
    assert envelope.payload["news"]["match_news_items"][0]["title"].startswith("Avai host")
    assert envelope.completeness == 1.0


def test_live_assisted_partial_context_from_backend() -> None:
    partial = {"motivation_narrative": _load_fixture("openclaw_bridge_live_context.json")["motivation_narrative"]}
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=_MockLiveBackend(context=partial),
        disable_live_backend=True,
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("skip probe"),
    ):
        envelope = enricher.enrich_context(_match_input())

    assert "live_backend_context_ok" in envelope.warnings
    assert "partial_context" in envelope.warnings
    assert "bridge_prototype_fallback" in envelope.warnings
    assert envelope.payload["motivation_narrative"]["matchwide"]["public_narrative_summary"]


def test_live_assisted_successful_backend_odds() -> None:
    live_odds = _load_fixture("openclaw_bridge_live_odds.json")
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=_MockLiveBackend(odds=live_odds),
        disable_live_backend=True,
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("skip probe"),
    ):
        envelope = enricher.enrich_odds(_match_input())

    assert "live_backend_odds_ok" in envelope.warnings
    assert "bridge_prototype_odds" not in envelope.warnings
    assert envelope.payload["markets"]["home_win"]["odds_value"] == 2.35
    assert envelope.completeness >= 0.5


def test_live_assisted_partial_odds_backend_unavailable() -> None:
    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=NoopLiveBackend(),
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("down"),
    ):
        envelope = enricher.enrich_odds(_match_input())

    assert "backend_unavailable" in envelope.warnings
    assert "bridge_prototype_fallback" in envelope.warnings
    assert envelope.payload["markets"]["double_chance_1x"]["bookmaker_name"] == "bridge_stub"


def test_live_assisted_backend_invalid_payload() -> None:
    class _BadBackend:
        def fetch_context_blocks(self, inp: BridgeMatchInput) -> LiveBackendResult:
            return LiveBackendResult(data={"unexpected": True}, warnings=[], source="bad")

        def fetch_odds_markets(self, inp: BridgeMatchInput) -> LiveBackendResult:
            return LiveBackendResult(data={"foo": "bar"}, warnings=[], source="bad")

    enricher = BridgeEnricher(
        mode=BridgeMode.LIVE_ASSISTED,
        openclaw_gateway_url="http://gateway.local",
        backend_client=_BadBackend(),
        disable_live_backend=True,
    )
    with patch(
        "football_agent.openclaw_bridge.enricher.requests.get",
        side_effect=requests.ConnectionError("skip probe"),
    ):
        envelope = enricher.enrich_context(_match_input())

    assert "backend_invalid_payload" in envelope.warnings
    assert "bridge_prototype_fallback" in envelope.warnings


def test_openclaw_chat_backend_parses_json_content() -> None:
    from football_agent.openclaw_bridge.backend_client import OpenClawChatBackend

    backend = OpenClawChatBackend("http://gateway.local", model="test")
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(_load_fixture("openclaw_bridge_live_context.json")),
                },
            },
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps(payload)
    mock_resp.json.return_value = payload

    with patch.object(backend._session, "post", return_value=mock_resp):
        result = backend.fetch_context_blocks(_match_input())

    assert result.data is not None
    assert "motivation_narrative" in result.data
    assert result.source == "openclaw_chat"


def test_enrichment_config_prefers_bridge_url() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://localhost:8787"):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://localhost:18789"):
            assert resolve_openclaw_base_url() == "http://localhost:8787"
            assert enrichment_uses_bridge() is True


def test_enrichment_config_legacy_when_bridge_unset() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://localhost:18789"):
            assert resolve_openclaw_base_url() == "http://localhost:18789"
            assert enrichment_uses_bridge() is False
