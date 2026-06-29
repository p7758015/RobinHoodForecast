"""
Resolve live enrichment URLs and routing for OpenClaw-first architecture.

Priority for enrichment HTTP base URL:

1. Explicit override (pipeline / CLI)
2. ``OPENCLAW_BRIDGE_BASE_URL`` (stable JSON bridge — preferred when set)
3. ``OPENCLAW_BASE_URL`` (legacy direct OpenClaw gateway)
4. ``OPENCLAW_CONTEXT_BASE_URL`` (legacy alias)

Odds URL resolution:

1. Explicit ``ODDS_SERVICE_URL`` → separate odds service (legacy escape hatch)
2. Else if ``OPENCLAW_PROVIDES_ODDS`` and OpenClaw base set → same base as OpenClaw
3. Else → odds not configured (not an error until OpenClaw exists)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from football_agent import config

logger = logging.getLogger(__name__)
from football_agent.services.enrichment_contract import (
    ENRICHMENT_MODE_NOT_CONFIGURED,
    ENRICHMENT_MODE_ODDS_SEPARATE,
    ENRICHMENT_MODE_SPLIT,
    ENRICHMENT_MODE_UNIFIED,
    ODDS_SOURCE_NONE,
    ODDS_SOURCE_OPENCLAW,
    ODDS_SOURCE_SEPARATE,
)


def resolve_openclaw_bridge_base_url() -> Optional[str]:
    """Configured OpenClaw bridge base URL (if any)."""
    return config.OPENCLAW_BRIDGE_BASE_URL


def resolve_openclaw_base_url(override: Optional[str] = None) -> Optional[str]:
    """Effective enrichment HTTP base (bridge preferred, then legacy gateway)."""
    if override is not None:
        url = override.strip().rstrip("/")
        return url or None
    if config.OPENCLAW_BRIDGE_BASE_URL:
        return config.OPENCLAW_BRIDGE_BASE_URL
    for candidate in (config.OPENCLAW_BASE_URL, config.OPENCLAW_CONTEXT_BASE_URL):
        if candidate:
            return candidate
    return None


def resolve_legacy_openclaw_gateway_url() -> Optional[str]:
    """Direct OpenClaw gateway URL (without bridge); for diagnostics only."""
    for candidate in (config.OPENCLAW_GATEWAY_URL, config.OPENCLAW_BASE_URL, config.OPENCLAW_CONTEXT_BASE_URL):
        if candidate:
            return candidate
    return None


def enrichment_uses_bridge(*, base_url: Optional[str] = None) -> bool:
    effective = resolve_openclaw_base_url(base_url) if base_url is None else base_url
    bridge = config.OPENCLAW_BRIDGE_BASE_URL
    return bool(bridge and effective and effective.rstrip("/") == bridge.rstrip("/"))


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


def probe_url_health(url: str, *, timeout_s: float = 5.0) -> bool:
    """Lightweight GET ``/health`` probe; False on network/HTTP errors."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return False
    try:
        resp = requests.get(f"{base}/health", timeout=timeout_s)
        return resp.status_code < 400
    except requests.RequestException:
        return False


def is_direct_gateway_url(url: Optional[str]) -> bool:
    """
    True when enrichment should use OpenClaw chat gateway (not bridge JSON /v1/context).

    Bridge base URL is never treated as direct gateway.
    """
    if not url:
        return False
    norm = url.rstrip("/")
    bridge = config.OPENCLAW_BRIDGE_BASE_URL
    if bridge and norm == bridge.rstrip("/"):
        return False
    gateway = resolve_legacy_openclaw_gateway_url()
    if gateway and norm == gateway.rstrip("/"):
        return True
    if not bridge:
        return bool(norm)
    return False


@dataclass(frozen=True)
class EnrichmentRoutingResolution:
    """Resolved routing plus live transport selection (bridge vs direct gateway)."""

    routing: EnrichmentRouting
    enrichment_backend: str  # bridge | direct_gateway | unavailable | none
    base_url_used: Optional[str]
    warnings: Tuple[str, ...] = ()


def resolve_enrichment_routing_with_fallback(
    *,
    openclaw_url_override: Optional[str] = None,
    odds_url_override: Optional[str] = None,
    skip_openclaw: bool = False,
    skip_odds: bool = False,
    mode_override: Optional[str] = None,
    health_timeout_s: float = 5.0,
) -> EnrichmentRoutingResolution:
    """
    Resolve enrichment routing with live health probes.

    Priority:
    1. Explicit ``openclaw_url_override`` (no bridge→gateway fallback)
    2. Bridge healthy → bridge HTTP
    3. Bridge down + gateway healthy → direct gateway (chat backend)
    4. Neither reachable → unavailable (fail-soft at fetch layer)
    """
    if skip_openclaw:
        routing = resolve_enrichment_routing(
            openclaw_url_override=openclaw_url_override,
            odds_url_override=odds_url_override,
            skip_openclaw=True,
            skip_odds=skip_odds,
            mode_override=mode_override,
        )
        return EnrichmentRoutingResolution(routing, "none", None, ())

    warnings: List[str] = []
    bridge = config.OPENCLAW_BRIDGE_BASE_URL
    gateway = resolve_legacy_openclaw_gateway_url()

    if openclaw_url_override is not None:
        url = openclaw_url_override.strip().rstrip("/") or None
        routing = resolve_enrichment_routing(
            openclaw_url_override=url,
            odds_url_override=odds_url_override,
            skip_openclaw=False,
            skip_odds=skip_odds,
            mode_override=mode_override,
        )
        if not url:
            return EnrichmentRoutingResolution(routing, "unavailable", None, ())
        backend = "bridge" if enrichment_uses_bridge(base_url=url) else (
            "direct_gateway" if is_direct_gateway_url(url) else "openclaw"
        )
        return EnrichmentRoutingResolution(routing, backend, url, ())

    if bridge:
        if probe_url_health(bridge, timeout_s=health_timeout_s):
            routing = resolve_enrichment_routing(
                odds_url_override=odds_url_override,
                skip_openclaw=False,
                skip_odds=skip_odds,
                mode_override=mode_override,
            )
            return EnrichmentRoutingResolution(routing, "bridge", bridge, ())
        warnings.append("openclaw_bridge_unavailable")
        if gateway and probe_url_health(gateway, timeout_s=health_timeout_s):
            routing = resolve_enrichment_routing(
                openclaw_url_override=gateway,
                odds_url_override=odds_url_override,
                skip_openclaw=False,
                skip_odds=skip_odds,
                mode_override=mode_override,
            )
            warnings.append("openclaw_fallback_direct_gateway")
            logger.info(
                "OpenClaw bridge unreachable at %s — using direct gateway %s",
                bridge,
                gateway,
            )
            return EnrichmentRoutingResolution(
                routing,
                "direct_gateway",
                gateway,
                tuple(warnings),
            )
        routing = resolve_enrichment_routing(
            odds_url_override=odds_url_override,
            skip_openclaw=False,
            skip_odds=skip_odds,
            mode_override=mode_override,
        )
        return EnrichmentRoutingResolution(routing, "unavailable", bridge, tuple(warnings))

    if gateway and probe_url_health(gateway, timeout_s=health_timeout_s):
        routing = resolve_enrichment_routing(
            openclaw_url_override=gateway,
            odds_url_override=odds_url_override,
            skip_openclaw=False,
            skip_odds=skip_odds,
            mode_override=mode_override,
        )
        return EnrichmentRoutingResolution(routing, "direct_gateway", gateway, ())

    routing = resolve_enrichment_routing(
        odds_url_override=odds_url_override,
        skip_openclaw=False,
        skip_odds=skip_odds,
        mode_override=mode_override,
    )
    base = routing.openclaw_base_url
    if base:
        return EnrichmentRoutingResolution(
            routing,
            "unavailable",
            base,
            ("openclaw_backend_unreachable",),
        )
    return EnrichmentRoutingResolution(routing, "unavailable", None, ("openclaw_not_configured",))
