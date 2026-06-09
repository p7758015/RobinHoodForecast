"""
Resolve live enrichment URLs and routing for OpenClaw-first architecture.

Priority for OpenClaw base URL:

1. Explicit override (pipeline / CLI)
2. ``OPENCLAW_BASE_URL`` (unified enrichment backend — target v1)
3. ``OPENCLAW_CONTEXT_BASE_URL`` (legacy alias, backward compatible)

Odds URL resolution:

1. Explicit ``ODDS_SERVICE_URL`` → separate odds service (legacy escape hatch)
2. Else if ``OPENCLAW_PROVIDES_ODDS`` and OpenClaw base set → same base as OpenClaw
3. Else → odds not configured (not an error until OpenClaw exists)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from football_agent import config
from football_agent.services.enrichment_contract import (
    ENRICHMENT_MODE_NOT_CONFIGURED,
    ENRICHMENT_MODE_ODDS_SEPARATE,
    ENRICHMENT_MODE_SPLIT,
    ENRICHMENT_MODE_UNIFIED,
    ODDS_SOURCE_NONE,
    ODDS_SOURCE_OPENCLAW,
    ODDS_SOURCE_SEPARATE,
)


def resolve_openclaw_base_url(override: Optional[str] = None) -> Optional[str]:
    """Effective OpenClaw enrichment base (context + optional odds)."""
    if override is not None:
        url = override.strip().rstrip("/")
        return url or None
    for candidate in (config.OPENCLAW_BASE_URL, config.OPENCLAW_CONTEXT_BASE_URL):
        if candidate:
            return candidate
    return None


def resolve_enrichment_mode(*, mode_override: Optional[str] = None) -> str:
    """
    Resolve transport mode.

    ``mode_override``: ``split`` | ``unified`` | ``auto`` | None (treat as auto).
    """
    choice = (mode_override or "auto").strip().lower()
    if choice == "split":
        return ENRICHMENT_MODE_SPLIT
    if choice == "unified":
        return ENRICHMENT_MODE_UNIFIED
    if config.OPENCLAW_ENRICHMENT_MODE == "unified":
        return ENRICHMENT_MODE_UNIFIED
    return ENRICHMENT_MODE_SPLIT


@dataclass(frozen=True)
class EnrichmentRouting:
    """Resolved enrichment routing for one pipeline run."""

    openclaw_base_url: Optional[str]
    context_base_url: Optional[str]
    odds_base_url: Optional[str]
    enrichment_mode: str
    odds_source: str
    odds_separate_service: bool
    openclaw_provides_odds: bool
    configured: bool

    @property
    def openclaw_configured(self) -> bool:
        return bool(self.openclaw_base_url)

    @property
    def odds_configured(self) -> bool:
        return bool(self.odds_base_url)


def resolve_enrichment_routing(
    *,
    openclaw_url_override: Optional[str] = None,
    odds_url_override: Optional[str] = None,
    skip_openclaw: bool = False,
    skip_odds: bool = False,
    mode_override: Optional[str] = None,
) -> EnrichmentRouting:
    openclaw_base = None if skip_openclaw else resolve_openclaw_base_url(openclaw_url_override)
    mode = resolve_enrichment_mode(mode_override=mode_override)
    if not openclaw_base:
        mode = ENRICHMENT_MODE_NOT_CONFIGURED

    openclaw_provides_odds = config.OPENCLAW_PROVIDES_ODDS

    explicit_odds = None if skip_odds else (odds_url_override or config.ODDS_SERVICE_URL)
    explicit_odds = (explicit_odds or "").strip().rstrip("/") or None

    odds_separate = False
    odds_base: Optional[str] = None
    odds_source = ODDS_SOURCE_NONE

    if explicit_odds:
        odds_base = explicit_odds
        odds_separate = True
        odds_source = ODDS_SOURCE_SEPARATE
        if mode == ENRICHMENT_MODE_NOT_CONFIGURED:
            mode = ENRICHMENT_MODE_ODDS_SEPARATE
    elif not skip_odds and openclaw_base and openclaw_provides_odds:
        odds_base = openclaw_base
        odds_source = ODDS_SOURCE_OPENCLAW

    context_base = openclaw_base

    return EnrichmentRouting(
        openclaw_base_url=openclaw_base,
        context_base_url=context_base,
        odds_base_url=odds_base,
        enrichment_mode=mode,
        odds_source=odds_source,
        odds_separate_service=odds_separate,
        openclaw_provides_odds=openclaw_provides_odds,
        configured=bool(openclaw_base or odds_base),
    )
