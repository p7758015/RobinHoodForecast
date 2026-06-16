"""Source registry tests."""

from __future__ import annotations

from football_agent.collectors.contracts import BlockCollectionResult, utc_now
from football_agent.registries.source_registry import DEFAULT_SOURCE_REGISTRY


def test_registry_has_phase_a_blocks() -> None:
    for block in ("match_meta", "teams", "form", "odds"):
        policy = DEFAULT_SOURCE_REGISTRY.policy_for(block)
        assert policy.primary == "flashscore"
        assert not policy.fallbacks


def test_no_fallback_when_ok() -> None:
    result = BlockCollectionResult(
        block="teams",
        status="ok",
        confidence=0.8,
        source="flashscore",
        collected_at_utc=utc_now(),
    )
    assert not DEFAULT_SOURCE_REGISTRY.should_fallback(result)


def test_deny_openclaw_in_registry() -> None:
    assert "openclaw" in DEFAULT_SOURCE_REGISTRY.deny_sources
