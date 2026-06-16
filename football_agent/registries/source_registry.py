"""Source priority registry (Phase A — flashscore-only blocks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Tuple

from football_agent.collectors.contracts import BlockCollectionResult


@dataclass(frozen=True)
class SourcePolicy:
    block: str
    primary: str
    fallbacks: Tuple[str, ...] = ()
    min_confidence: float = 0.5
    max_fallback_attempts: int = 1


@dataclass(frozen=True)
class SourceRegistry:
    policies: Dict[str, SourcePolicy]
    deny_sources: FrozenSet[str] = field(default_factory=frozenset)

    def policy_for(self, block: str) -> SourcePolicy:
        if block not in self.policies:
            raise KeyError(f"No source policy for block: {block}")
        return self.policies[block]

    def should_fallback(self, result: BlockCollectionResult) -> bool:
        policy = self.policy_for(result.block)
        if not policy.fallbacks:
            return False
        if result.status == "ok" and result.confidence >= policy.min_confidence:
            return False
        return result.status in ("missing", "failed", "partial")


DEFAULT_SOURCE_REGISTRY = SourceRegistry(
    policies={
        "match_meta": SourcePolicy(block="match_meta", primary="flashscore"),
        "teams": SourcePolicy(block="teams", primary="flashscore"),
        "form": SourcePolicy(block="form", primary="flashscore"),
        "odds": SourcePolicy(block="odds", primary="flashscore"),
    },
    deny_sources=frozenset({"openclaw", "llm_odds"}),
)
