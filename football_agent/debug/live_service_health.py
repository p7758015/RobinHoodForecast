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
    probes.append(check_openclaw_gateway(openclaw_url, timeout_s=timeout_s))
    legacy = check_openclaw_legacy_gateway(timeout_s=timeout_s)
    if legacy.url and legacy.url != (openclaw_url or resolve_openclaw_base_url() or ""):
        probes.append(legacy)
    return probes
