"""OpenClaw-first enrichment routing configuration tests."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.services.enrichment_config import (
    resolve_enrichment_routing,
    resolve_openclaw_base_url,
)
from football_agent.services.enrichment_contract import (
    ENRICHMENT_MODE_NOT_CONFIGURED,
    ENRICHMENT_MODE_ODDS_SEPARATE,
    ENRICHMENT_MODE_SPLIT,
    ODDS_SOURCE_NONE,
    ODDS_SOURCE_OPENCLAW,
    ODDS_SOURCE_SEPARATE,
)


def test_openclaw_base_url_prefers_bridge_over_gateway() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://bridge:8787"):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
            assert resolve_openclaw_base_url() == "http://bridge:8787"


def test_openclaw_base_url_prefers_openclaw_base() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_CONTEXT_BASE_URL", "http://legacy"):
                assert resolve_openclaw_base_url() == "http://oc"


def test_openclaw_base_url_falls_back_to_context_alias() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", None):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_CONTEXT_BASE_URL", "http://legacy"):
                assert resolve_openclaw_base_url() == "http://legacy"


def test_routing_not_configured_without_urls() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", None):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_CONTEXT_BASE_URL", None):
                with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None):
                    routing = resolve_enrichment_routing()
    assert routing.enrichment_mode == ENRICHMENT_MODE_NOT_CONFIGURED
    assert routing.odds_source == ODDS_SOURCE_NONE
    assert not routing.configured


def test_routing_bridge_first_odds_on_same_base() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", "http://bridge:8787"):
        with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_PROVIDES_ODDS", True):
                routing = resolve_enrichment_routing()
    assert routing.context_base_url == "http://bridge:8787"
    assert routing.odds_base_url == "http://bridge:8787"
    assert routing.odds_source == ODDS_SOURCE_OPENCLAW


def test_routing_openclaw_first_odds_on_same_base() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
            with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None):
                with patch("football_agent.services.enrichment_config.config.OPENCLAW_PROVIDES_ODDS", True):
                    routing = resolve_enrichment_routing()
    assert routing.enrichment_mode == ENRICHMENT_MODE_SPLIT
    assert routing.odds_base_url == "http://oc"
    assert routing.odds_source == ODDS_SOURCE_OPENCLAW
    assert not routing.odds_separate_service


def test_routing_separate_odds_service_override() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
            with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", "http://odds"):
                routing = resolve_enrichment_routing()
    assert routing.odds_base_url == "http://odds"
    assert routing.odds_source == ODDS_SOURCE_SEPARATE
    assert routing.odds_separate_service


def test_routing_odds_only_separate_service() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", None):
            with patch("football_agent.services.enrichment_config.config.OPENCLAW_CONTEXT_BASE_URL", None):
                with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", "http://odds"):
                    routing = resolve_enrichment_routing()
    assert routing.enrichment_mode == ENRICHMENT_MODE_ODDS_SEPARATE
    assert routing.odds_source == ODDS_SOURCE_SEPARATE


def test_routing_openclaw_provides_odds_disabled() -> None:
    with patch("football_agent.services.enrichment_config.config.OPENCLAW_BRIDGE_BASE_URL", None):
        with patch("football_agent.services.enrichment_config.config.OPENCLAW_BASE_URL", "http://oc"):
            with patch("football_agent.services.enrichment_config.config.ODDS_SERVICE_URL", None):
                with patch("football_agent.services.enrichment_config.config.OPENCLAW_PROVIDES_ODDS", False):
                    routing = resolve_enrichment_routing()
    assert routing.odds_base_url is None
    assert routing.odds_source == ODDS_SOURCE_NONE
