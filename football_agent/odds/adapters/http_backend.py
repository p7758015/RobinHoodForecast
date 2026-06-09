"""
HTTP adapter for self-hosted odds service (live enrichment).

Debug/CLI + Telegram runtime — separate from legacy API-Football odds path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urljoin

import requests

from football_agent.adapters.http_utils import apply_api_key, get_json, unwrap_dict_payload
from football_agent.odds.adapters.base import OddsAdapter
from football_agent.odds.adapters.errors import (
    OddsServiceConfigurationError,
    OddsServiceError,
    OddsServiceUnavailableError,
)


class HttpOddsAdapter(OddsAdapter):
    """
    Fetch raw odds payloads over HTTP.

    Default path: ``GET {base}/v1/odds?home=...&away=...&date=...&match_id=...&url=...``
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
        odds_path: str = "/v1/odds",
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s
        self._odds_path = odds_path
        self._session = session or requests.Session()
        apply_api_key(self._session, api_key)

    def _require_base(self) -> str:
        if not self._base_url:
            raise OddsServiceConfigurationError(
                "Odds service URL is not set. "
                "Set ODDS_SERVICE_URL in .env or pass odds_url override.",
            )
        return self._base_url

    def fetch_odds_raw(self, fixture_id_or_query: str) -> Dict[str, Any]:
        base = self._require_base()
        token = (fixture_id_or_query or "").strip()
        if not token:
            raise OddsServiceError("odds query token is empty")

        params = self._parse_query_token(token)
        url = urljoin(base + "/", self._odds_path.lstrip("/"))
        data = get_json(
            self._session,
            url,
            params=params,
            timeout_s=self._timeout,
            error_cls=OddsServiceUnavailableError,
        )
        if not isinstance(data, dict):
            raise OddsServiceError(
                f"Expected JSON object from odds service, got {type(data).__name__}",
            )
        raw = unwrap_dict_payload(data)
        if not raw:
            raise OddsServiceError("Empty odds service response")
        out = dict(raw)
        out.setdefault("backend_name", "http")
        return out

    @staticmethod
    def build_query_token(
        *,
        home: str,
        away: str,
        date: Optional[str] = None,
        competition: Optional[str] = None,
        competition_name: Optional[str] = None,
        kickoff_utc: Optional[str] = None,
        match_id: Optional[str] = None,
        match_url: Optional[str] = None,
    ) -> str:
        params: Dict[str, str] = {
            "home": home.strip(),
            "away": away.strip(),
        }
        if date:
            params["date"] = date.strip()
        if competition:
            params["competition"] = competition.strip()
        if competition_name:
            params["competition_name"] = competition_name.strip()
        if kickoff_utc:
            params["kickoff_utc"] = kickoff_utc.strip()
        if match_id:
            params["match_id"] = match_id.strip()
        if match_url:
            params["url"] = match_url.strip()
        return urlencode(params)

    @staticmethod
    def _parse_query_token(token: str) -> Dict[str, str]:
        stripped = token.strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise OddsServiceError(f"Invalid JSON query token: {e}") from e
            if not isinstance(obj, dict):
                raise OddsServiceError("JSON query token must be an object")
            return {str(k): str(v) for k, v in obj.items() if v is not None}

        parsed = parse_qs(stripped, keep_blank_values=False)
        return {k: (v[0] if v else "") for k, v in parsed.items()}
