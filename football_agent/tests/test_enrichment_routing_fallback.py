"""Enrichment routing fallback (bridge down → direct gateway)."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.services.enrichment_config import (
    resolve_enrichment_routing_with_fallback,
)


def test_fallback_to_direct_gateway_when_bridge_down() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://bridge:8787"):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_GATEWAY_URL", "http://gw:18789"):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://gw:18789"):
                with patch(
                    "football_agent.services.enrichment_config.probe_url_health",
                    side_effect=lambda url, **_: url.rstrip("/").endswith("18789"),
                ):
                    resolution = resolve_enrichment_routing_with_fallback()
    assert resolution.enrichment_backend == "direct_gateway"
    assert resolution.base_url_used == "http://gw:18789"
    assert "openclaw_bridge_unavailable" in resolution.warnings
    assert resolution.routing.context_base_url == "http://gw:18789"


def test_uses_bridge_when_healthy() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://bridge:8787"):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_GATEWAY_URL", "http://gw:18789"):
            with patch(
                "football_agent.services.enrichment_config.probe_url_health",
                side_effect=lambda url, **_: url.rstrip("/").endswith("8787"),
            ):
                resolution = resolve_enrichment_routing_with_fallback()
    assert resolution.enrichment_backend == "bridge"
    assert resolution.base_url_used == "http://bridge:8787"


def test_unavailable_when_both_down() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://bridge:8787"):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_GATEWAY_URL", "http://gw:18789"):
            with patch("football_agent.services.enrichment_config.probe_url_health", return_value=False):
                resolution = resolve_enrichment_routing_with_fallback()
    assert resolution.enrichment_backend == "unavailable"
    assert "openclaw_bridge_unavailable" in resolution.warnings
