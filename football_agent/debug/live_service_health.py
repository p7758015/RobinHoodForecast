"""
Stage 4: lightweight health probes for live debug dependencies (no pipeline side effects).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from football_agent import config
from football_agent.services.enrichment_config import (
    resolve_openclaw_base_url,
    resolve_openclaw_bridge_base_url,
    resolve_legacy_openclaw_gateway_url,
)


@dataclass(frozen=True)
class ServiceHealth:
    name: str
    url: str
    ok: bool
    status_code: Optional[int] = None
    detail: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "ok": self.ok,
            "status_code": self.status_code,
            "detail": (self.detail or "")[:300] or None,
            "error": self.error,
        }


def _probe_get(name: str, url: str, *, timeout_s: float = 5.0) -> ServiceHealth:
    try:
        resp = requests.get(url, timeout=timeout_s)
        body = (resp.text or "").strip()
        ok = resp.status_code < 400
        return ServiceHealth(
            name=name,
            url=url,
            ok=ok,
            status_code=resp.status_code,
            detail=body[:300],
        )
    except requests.RequestException as exc:
        return ServiceHealth(name=name, url=url, ok=False, error=str(exc))


def check_flashscore_scraper(
    base_url: Optional[str] = None,
    *,
    timeout_s: float = 5.0,
) -> ServiceHealth:
    base = (base_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
    if not base:
        return ServiceHealth(
            name="flashscore_scraper",
            url="",
            ok=False,
            error="FLASHSCORE_SCRAPER_URL not configured",
        )
    return _probe_get("flashscore_scraper", f"{base}/health", timeout_s=timeout_s)


def check_openclaw_bridge(
    base_url: Optional[str] = None,
    *,
    timeout_s: float = 5.0,
) -> ServiceHealth:
    base = (base_url or resolve_openclaw_bridge_base_url() or "").strip().rstrip("/")
    if not base:
        return ServiceHealth(
            name="openclaw_bridge",
            url="",
            ok=False,
            error="OPENCLAW_BRIDGE_BASE_URL not configured",
        )
    return _probe_get("openclaw_bridge", f"{base}/health", timeout_s=timeout_s)


def check_openclaw_gateway(
    base_url: Optional[str] = None,
    *,
    timeout_s: float = 5.0,
) -> ServiceHealth:
    base = resolve_openclaw_base_url(base_url)
    if not base:
        return ServiceHealth(
            name="openclaw_enrichment",
            url="",
            ok=False,
            error="OPENCLAW_BRIDGE_BASE_URL / OPENCLAW_BASE_URL not configured",
        )
    return _probe_get("openclaw_enrichment", f"{base}/health", timeout_s=timeout_s)


def check_openclaw_legacy_gateway(
    base_url: Optional[str] = None,
    *,
    timeout_s: float = 5.0,
) -> ServiceHealth:
    base = (base_url or resolve_legacy_openclaw_gateway_url() or "").strip().rstrip("/")
    if not base:
        return ServiceHealth(
            name="openclaw_legacy_gateway",
            url="",
            ok=False,
            error="OPENCLAW_GATEWAY_URL / OPENCLAW_BASE_URL not configured",
        )
    return _probe_get("openclaw_legacy_gateway", f"{base}/health", timeout_s=timeout_s)


def check_live_services(
    *,
    flashscore_url: Optional[str] = None,
    openclaw_url: Optional[str] = None,
    timeout_s: float = 5.0,
) -> List[ServiceHealth]:
    probes = [check_flashscore_scraper(flashscore_url, timeout_s=timeout_s)]
    bridge = check_openclaw_bridge(timeout_s=timeout_s)
    if bridge.url:
        probes.append(bridge)
    gateway = check_openclaw_legacy_gateway(timeout_s=timeout_s)
    if gateway.url:
        probes.append(gateway)
    enrichment = check_openclaw_gateway(openclaw_url, timeout_s=timeout_s)
    if enrichment.url and enrichment.url not in {p.url for p in probes}:
        probes.append(enrichment)
    return probes


def summarize_live_services(
    *,
    flashscore_url: Optional[str] = None,
    openclaw_url: Optional[str] = None,
    timeout_s: float = 5.0,
) -> Dict[str, Any]:
    """
    Aggregate service probes for operational smoke.

    ``all_ok`` when Flashscore is healthy and at least one OpenClaw path
    (bridge or direct gateway) responds — bridge down + gateway up is OK.
    """
    probes = check_live_services(
        flashscore_url=flashscore_url,
        openclaw_url=openclaw_url,
        timeout_s=timeout_s,
    )
    by_name = {p.name: p for p in probes}
    flashscore_ok = by_name.get("flashscore_scraper", ServiceHealth("flashscore_scraper", "", False)).ok
    bridge = by_name.get("openclaw_bridge")
    gateway = by_name.get("openclaw_legacy_gateway") or by_name.get("openclaw_enrichment")
    bridge_ok = bool(bridge and bridge.ok)
    gateway_ok = bool(gateway and gateway.ok)
    openclaw_ok = bridge_ok or gateway_ok
    effective_backend: Optional[str] = None
    if bridge_ok:
        effective_backend = "bridge"
    elif gateway_ok:
        effective_backend = "direct_gateway"
    return {
        "services": [p.to_dict() for p in probes],
        "flashscore_ok": flashscore_ok,
        "openclaw_ok": openclaw_ok,
        "openclaw_effective_backend": effective_backend,
        "all_ok": flashscore_ok and openclaw_ok,
    }
