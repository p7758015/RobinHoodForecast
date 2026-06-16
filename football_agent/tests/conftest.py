"""Shared pytest fixtures."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def legacy_pipeline_uses_non_collector_ingest(request: pytest.FixtureRequest):
    """
    OpenClaw/enrichment pipeline mock tests patch ``_fetch_facts`` only.

    When USE_COLLECTOR_LAYER=true (e.g. from .env), force legacy ingest path so
    those tests keep exercising enrichment merge/score — not collector HTTP.
    """
    module = getattr(request.module, "__name__", "")
    legacy_suffixes = (
        "test_live_flashscore_pipeline_openclaw",
        "test_live_adapters",
    )
    if not any(module == s or module.endswith(f".{s}") for s in legacy_suffixes):
        yield
        return

    with patch(
        "football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER",
        False,
    ):
        yield
