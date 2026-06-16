"""Thin Flashscore client for collector layer (reuses existing HTTP adapter)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from football_agent import config
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.path_sanitize import filesystem_safe_segment
from football_agent.paths import SNAPSHOTS_DIR

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BACKOFF_S = (1.0, 2.0)


class FlashscoreCollectorClient:
    """
    Fetches raw JSON from the self-hosted Flashscore scraper and maps to facts.

    Block collectors operate on enriched raw dicts; this client handles HTTP + persistence.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        raw_store_dir: Optional[Path] = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s or config.FLASHSCORE_SCRAPER_TIMEOUT_S
        self._max_attempts = max(1, max_attempts)
        self._raw_store_dir = raw_store_dir or (SNAPSHOTS_DIR / "collector_raw")
        self._adapter = HttpFlashscoreScraperAdapter(
            self._base_url,
            api_key=self._api_key,
            timeout_s=self._timeout,
        )
        self._ingestion = FlashscoreIngestionService(self._adapter)

    @property
    def adapter(self) -> HttpFlashscoreScraperAdapter:
        return self._adapter

    def fetch_match_raw_enriched(
        self,
        match_id_or_url: str,
        *,
        match_key: Optional[str] = None,
        block: str = "match",
    ) -> Dict[str, Any]:
        raw = self._fetch_with_retry(lambda: self._adapter.fetch_match_raw(match_id_or_url))
        enriched = enrich_http_flashscore_raw(raw)
        ref = self._save_raw(match_key or self._derive_match_key(enriched), block, enriched)
        enriched["_collector_raw_ref"] = ref
        return enriched

    def fetch_matches_for_date_raw(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raw_list = self._fetch_with_retry(
            lambda: self._adapter.fetch_matches_for_date(date_str, competition_code),
        )
        return [enrich_http_flashscore_raw(r) for r in raw_list]

    def map_to_facts(self, raw: Dict[str, Any]) -> FlashscoreMatchFacts:
        return self._ingestion._map_raw_to_facts(raw)  # noqa: SLF001 — intentional reuse

    def save_parse_report(
        self,
        match_key: str,
        block: str,
        report: Dict[str, Any],
    ) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_key = filesystem_safe_segment(match_key)
        safe_block = filesystem_safe_segment(block, max_len=64)
        dest_dir = self._raw_store_dir / safe_key / safe_block
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{ts}_parse_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _fetch_with_retry(self, fn):
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_attempts):
            try:
                return fn()
            except (FlashscoreScraperUnavailableError, requests.RequestException) as exc:
                last_exc = exc
                if attempt + 1 >= self._max_attempts:
                    break
                delay = DEFAULT_BACKOFF_S[min(attempt, len(DEFAULT_BACKOFF_S) - 1)]
                logger.warning("Flashscore fetch retry %s after %ss: %s", attempt + 1, delay, exc)
                time.sleep(delay)
        raise last_exc or FlashscoreScraperError("Flashscore fetch failed")

    def _save_raw(self, match_key: str, block: str, payload: Dict[str, Any]) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_key = filesystem_safe_segment(match_key)
        safe_block = filesystem_safe_segment(block, max_len=64)
        dest_dir = self._raw_store_dir / safe_key / safe_block
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{ts}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    @staticmethod
    def _derive_match_key(raw: Dict[str, Any]) -> str:
        mid = str(raw.get("match_id") or raw.get("id") or "unknown")
        home = str(raw.get("home_team_name") or raw.get("home") or "home")
        away = str(raw.get("away_team_name") or raw.get("away") or "away")
        return f"{mid}:{home}:{away}"
