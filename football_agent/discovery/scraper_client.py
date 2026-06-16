"""HTTP client for Flashscore scraper discovery endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from football_agent.adapters.http_utils import apply_api_key, get_json, normalize_match_list
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperUnavailableError,
)


class FlashscoreDiscoveryClient:
    """
    Thin wrapper over scraper discovery API:
    - GET /v1/competitions/search?q=
    - GET /v1/competitions/fixtures?competition_url=&date_from=&date_to=
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout_s: float = 90.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s
        self._session = session or requests.Session()
        apply_api_key(self._session, api_key)

    def _require_base(self) -> str:
        if not self._base_url:
            raise FlashscoreScraperConfigurationError(
                "Flashscore scraper URL is not set (FLASHSCORE_SCRAPER_URL).",
            )
        return self._base_url

    def search_competitions(self, query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        base = self._require_base()
        data = get_json(
            self._session,
            f"{base}/v1/competitions/search",
            params={"q": query.strip(), "limit": int(limit)},
            timeout_s=self._timeout,
            error_cls=FlashscoreScraperUnavailableError,
        )
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict)]
        return []

    def fetch_competition_fixtures(
        self,
        competition_url: str,
        *,
        date_from: str,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        base = self._require_base()
        params: Dict[str, str] = {
            "competition_url": competition_url.strip(),
            "date_from": date_from,
            "date_to": date_to or date_from,
        }
        data = get_json(
            self._session,
            f"{base}/v1/competitions/fixtures",
            params=params,
            timeout_s=self._timeout,
            error_cls=FlashscoreScraperUnavailableError,
        )
        if isinstance(data, dict):
            matches = data.get("matches")
            if isinstance(matches, list):
                return [m for m in matches if isinstance(m, dict)]
        return normalize_match_list(data)
