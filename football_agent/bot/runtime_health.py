"""
Startup validation, dependency probes, and /health reporting for Telegram bot runtime.

Transport-agnostic — no python-telegram-bot imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests

from football_agent import config
from football_agent.services.enrichment_config import resolve_enrichment_routing
from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs
from football_agent.storage.sqlite_runtime import journal_mode, open_sqlite_connection, ping_database

logger = logging.getLogger(__name__)


@dataclass
class DependencyStatus:
    name: str
    configured: bool
    required: bool
    reachable: Optional[bool] = None  # None = not probed
    detail: str = ""

    @property
    def ok_for_runtime(self) -> bool:
        if self.required and not self.configured:
            return False
        if self.configured and self.reachable is False:
            return False
        return True

    @property
    def label(self) -> str:
        if not self.configured:
            return "not configured"
        if self.reachable is True:
            return "ok"
        if self.reachable is False:
            return "unreachable"
        return "configured"


@dataclass
class StartupReport:
    ready: bool
    critical_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    degraded_modes: List[str] = field(default_factory=list)
    dependencies: Dict[str, DependencyStatus] = field(default_factory=dict)

    def log_summary(self) -> None:
        if self.critical_errors:
            for err in self.critical_errors:
                logger.error("Startup critical: %s", err)
        for warn in self.warnings:
            logger.warning("Startup warning: %s", warn)
        if self.degraded_modes:
            logger.warning("Degraded modes: %s", ", ".join(self.degraded_modes))
        for dep in self.dependencies.values():
            logger.info(
                "Dependency %s: configured=%s reachable=%s required=%s (%s)",
                dep.name,
                dep.configured,
                dep.reachable,
                dep.required,
                dep.detail or dep.label,
            )


def _probe_http_health(base_url: str, *, timeout_s: float) -> bool:
    url = urljoin(base_url.rstrip("/") + "/", "health")
    try:
        resp = requests.get(url, timeout=timeout_s)
        return resp.status_code == 200
    except requests.RequestException as exc:
        logger.debug("Health probe failed for %s: %s", url, exc)
        return False


def validate_startup(*, probe_dependencies: bool = True) -> StartupReport:
    """
    Validate bot runtime configuration.

    Only ``TELEGRAM_BOT_TOKEN`` is a hard startup failure.
    Flashscore / OpenClaw / odds may be missing → degraded mode, bot still starts.
    """
    critical: List[str] = []
    warnings: List[str] = []
    degraded: List[str] = []
    deps: Dict[str, DependencyStatus] = {}
    timeout = config.BOT_HEALTH_PROBE_TIMEOUT_S

    token_ok = bool(config.TELEGRAM_BOT_TOKEN)
    deps["telegram"] = DependencyStatus(
        name="telegram",
        configured=token_ok,
        required=True,
        reachable=None,
        detail="token present" if token_ok else "TELEGRAM_BOT_TOKEN missing",
    )
    if not token_ok:
        critical.append("TELEGRAM_BOT_TOKEN is required")

    fs_url = config.FLASHSCORE_SCRAPER_URL
    fs_reachable = _probe_http_health(fs_url, timeout_s=timeout) if probe_dependencies and fs_url else None
    deps["flashscore"] = DependencyStatus(
        name="flashscore",
        configured=bool(fs_url),
        required=False,
        reachable=fs_reachable,
        detail=fs_url or "FLASHSCORE_SCRAPER_URL not set",
    )
    if not fs_url:
        degraded.append("no_flashscore")
        warnings.append("Flashscore scraper not configured — match analysis unavailable")
    elif fs_reachable is False:
        degraded.append("flashscore_unreachable")
        warnings.append("Flashscore scraper configured but /health probe failed")

    routing = resolve_enrichment_routing()
    oc_url = routing.openclaw_base_url
    oc_reachable = _probe_http_health(oc_url, timeout_s=timeout) if probe_dependencies and oc_url else None
    deps["openclaw_enrichment"] = DependencyStatus(
        name="openclaw_enrichment",
        configured=bool(oc_url),
        required=False,
        reachable=oc_reachable,
        detail=(
            f"{oc_url} mode={routing.enrichment_mode}"
            if oc_url
            else "OPENCLAW_BASE_URL / OPENCLAW_CONTEXT_BASE_URL not set"
        ),
    )
    deps["openclaw_context"] = deps["openclaw_enrichment"]
    if not oc_url:
        degraded.append("no_openclaw_enrichment")
    elif oc_reachable is False:
        degraded.append("openclaw_unreachable")
        warnings.append("OpenClaw enrichment configured but /health probe failed")

    odds_separate = routing.odds_separate_service
    odds_url = config.ODDS_SERVICE_URL if odds_separate else None
    odds_reachable = (
        _probe_http_health(odds_url, timeout_s=timeout) if probe_dependencies and odds_url else None
    )
    deps["odds"] = DependencyStatus(
        name="odds",
        configured=bool(odds_separate),
        required=False,
        reachable=odds_reachable,
        detail=(
            f"separate: {odds_url}"
            if odds_separate
            else (
                "via OpenClaw base (OPENCLAW_PROVIDES_ODDS)"
                if routing.openclaw_provides_odds and oc_url
                else "not configured (expected from OpenClaw when deployed)"
            )
        ),
    )
    if odds_separate and not odds_url:
        degraded.append("no_odds_separate")
    elif odds_separate and odds_reachable is False:
        degraded.append("odds_unreachable")
        warnings.append("Separate odds service configured but /health probe failed")

    ensure_runtime_dirs()
    db_ok = ping_database(DEFAULT_DB_PATH)
    jmode: Optional[str] = None
    if db_ok:
        conn = open_sqlite_connection(DEFAULT_DB_PATH)
        try:
            jmode = journal_mode(conn)
        finally:
            conn.close()
    deps["database"] = DependencyStatus(
        name="database",
        configured=True,
        required=True,
        reachable=db_ok,
        detail=f"path={DEFAULT_DB_PATH} journal_mode={jmode or 'unknown'}",
    )
    if not db_ok:
        critical.append(f"Database not writable at {DEFAULT_DB_PATH}")

    ready = len(critical) == 0
    return StartupReport(
        ready=ready,
        critical_errors=critical,
        warnings=warnings,
        degraded_modes=degraded,
        dependencies=deps,
    )


def format_health_message(report: Optional[StartupReport] = None) -> str:
    """User-facing /health text."""
    report = report or validate_startup(probe_dependencies=True)
    lines = ["Статус бота (runtime):"]

    for key in ("telegram", "flashscore", "openclaw_enrichment", "odds", "database"):
        dep = report.dependencies.get(key)
        if dep is None:
            continue
        req = " (required)" if dep.required else " (optional)"
        lines.append(f"• {dep.name}{req}: {dep.label}")
        if dep.detail and dep.label != "ok":
            lines.append(f"  {dep.detail}")

    if report.degraded_modes:
        lines.append("")
        lines.append("Degraded: " + ", ".join(report.degraded_modes))
    else:
        lines.append("")
        lines.append("Degraded: none")

    lines.append("")
    lines.append("Pipeline: v2 single-match (Flashscore + OpenClaw enrichment)")
    return "\n".join(lines)
