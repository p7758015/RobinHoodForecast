"""OpenClaw-primary enrichment routing tests."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.services.openclaw_primary_enrichment import (
    brave_fallback_allowed,
    openclaw_primary_enrichment,
)
from football_agent.services.openclaw_news_enrichment import brave_news_enabled


def test_openclaw_primary_disables_brave_by_default() -> None:
    with patch("football_agent.services.openclaw_primary_enrichment.config.OPENCLAW_PRIMARY_ENRICHMENT", True):
        with patch("football_agent.services.openclaw_primary_enrichment.config.USE_BRAVE_NEWS_ENRICHMENT", False):
            with patch("football_agent.services.openclaw_primary_enrichment.config.USE_BRAVE_NEWS_FALLBACK", False):
                with patch("football_agent.services.openclaw_primary_enrichment.config.USE_OPENCLAW_NEWS", True):
                    with patch("football_agent.services.openclaw_primary_enrichment.config.BRAVE_SEARCH_API_KEY", "k"):
                        assert openclaw_primary_enrichment() is True
                        assert brave_fallback_allowed() is False
                        assert brave_news_enabled() is False


def test_brave_explicit_fallback_when_primary() -> None:
    with patch("football_agent.services.openclaw_primary_enrichment.config.OPENCLAW_PRIMARY_ENRICHMENT", True):
        with patch("football_agent.services.openclaw_primary_enrichment.config.USE_BRAVE_NEWS_ENRICHMENT", True):
            with patch("football_agent.services.openclaw_primary_enrichment.config.BRAVE_SEARCH_API_KEY", "k"):
                assert brave_fallback_allowed() is True
                assert brave_news_enabled() is True
