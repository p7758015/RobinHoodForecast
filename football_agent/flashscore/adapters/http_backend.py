"""
HTTP adapter for a self-hosted Flashscore scraper (e.g. gustavofariaa/FlashscoreScraping).

Debug/CLI only — not wired into Telegram or app_pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urljoin

import requests

from football_agent.adapters.http_utils import apply_api_key, get_json, normalize_match_list, unwrap_dict_payload
from football_agent.flashscore.adapters.base import FlashscoreScraperAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)


class HttpFlashscoreScraperAdapter(FlashscoreScraperAdapter):
    """
    Fetch raw Flashscore payloads over HTTP.

    Default paths (override via constructor if your scraper differs):

    - ``GET {base}/v1/match?url=...`` or ``?match_id=...``
    - ``GET {base}/v1/matches?date=YYYY-MM-DD&competition_code=PL``
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,
        session: Optional[requests.Session] = None,
        match_path: str = "/v1/match",
        date_path: str = "/v1/matches",
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s
        self._match_path = match_path
        self._date_path = date_path
        self._session = session or requests.Session()
        apply_api_key(self._session, api_key)

    def _require_base(self) -> str:
        if not self._base_url:
            raise FlashscoreScraperConfigurationError(
                "Flashscore scraper URL is not set. "
                "Set FLASHSCORE_SCRAPER_URL in .env or pass --flashscore-url.",
            )
        return self._base_url

    def fetch_match_raw(self, match_id_or_url: str) -> Dict[str, Any]:
        base = self._require_base()
        ref = (match_id_or_url or "").strip()
        if not ref:
            raise FlashscoreScraperError("match_id_or_url is empty")

        params: Dict[str, str]
        if ref.startswith("http://") or ref.startswith("https://"):
            params = {"url": ref}
        else:
            params = {"match_id": ref}

        url = urljoin(base + "/", self._match_path.lstrip("/"))
        data = get_json(
            self._session,
            url,
            params=params,
            timeout_s=self._timeout,
            error_cls=FlashscoreScraperUnavailableError,
        )
        return self._normalize_single_record(data)

    def fetch_matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
        *,
        competition_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        base = self._require_base()
        params: Dict[str, str] = {"date": date_str}
        if competition_url:
            params["competition_url"] = competition_url.strip()
        elif competition_code:
            params["competition_code"] = competition_code.upper()

        url = urljoin(base + "/", self._date_path.lstrip("/"))
        data = get_json(
            self._session,
            url,
            params=params,
            timeout_s=self._timeout,
            error_cls=FlashscoreScraperUnavailableError,
        )
        records = normalize_match_list(data)
        if competition_code and not competition_url:
            code = competition_code.upper()
            records = [
                r
                for r in records
                if str(r.get("competition_code") or "").upper() == code
            ]
        return [self._normalize_single_record(r) for r in records]

    def search_competitions(self, query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient

        client = FlashscoreDiscoveryClient(
            self._require_base(),
            api_key=self._api_key,
            timeout_s=self._timeout,
            session=self._session,
        )
        return client.search_competitions(query, limit=limit)

    def fetch_competition_fixtures(
        self,
        competition_url: str,
        *,
        date_from: str,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient

        client = FlashscoreDiscoveryClient(
            self._require_base(),
            api_key=self._api_key,
            timeout_s=self._timeout,
            session=self._session,
        )
        rows = client.fetch_competition_fixtures(
            competition_url,
            date_from=date_from,
            date_to=date_to,
        )
        return [self._normalize_single_record(r) for r in rows]

    def _normalize_single_record(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise FlashscoreScraperError(
                f"Expected JSON object from scraper, got {type(data).__name__}",
            )
        raw = unwrap_dict_payload(data)
        if not raw:
            raise FlashscoreScraperError("Empty scraper response")
        out = enrich_http_flashscore_raw(dict(raw))
        out.setdefault("scraper_backend_name", "http")
        out.setdefault("collected_at_utc", datetime.now(timezone.utc).isoformat())
        return out
