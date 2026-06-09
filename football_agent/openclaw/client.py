"""HTTP (or mock) client: pull OpenClaw JSON → OpenClawMatchPayload."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from football_agent import config
from football_agent.openclaw.models import OpenClawMatchPayload

logger = logging.getLogger(__name__)


class OpenClawClientError(Exception):
    """Base class for OpenClaw client failures."""


class OpenClawConfigurationError(OpenClawClientError):
    """Missing OPENCLAW_BASE_URL or similar."""


class OpenClawClient:
    """
    Minimal REST bridge. Endpoints are conventions — adjust when your OpenClaw API is fixed.

    Expected (illustrative):

    - ``GET /v1/matches/{openclaw_event_id}`` → single match envelope
    - ``GET /v1/matches?date=YYYY-MM-DD&competition_code=PL`` → list envelope

    Responses may be wrapped as ``{"payload": {...}}`` or raw OpenClawMatchPayload JSON.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        *,
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._base_url = (base_url or config.OPENCLAW_BASE_URL or "").rstrip("/")
        self._api_key = api_key if api_key is not None else config.OPENCLAW_API_KEY
        self._timeout = timeout_s
        self._session = session or requests.Session()
        if self._api_key:
            self._session.headers.setdefault("Authorization", f"Bearer {self._api_key}")

    def _require_base(self) -> str:
        if not self._base_url:
            raise OpenClawConfigurationError(
                "OPENCLAW_BASE_URL is not set; pass base_url= or configure .env.",
            )
        return self._base_url

    def fetch_match_raw(self, openclaw_event_id: str) -> Dict[str, Any]:
        base = self._require_base()
        url = f"{base}/v1/matches/{openclaw_event_id}"
        return self._get_json(url)

    def fetch_matches_for_date_raw(self, date_str: str, competition_code: Optional[str] = None) -> List[Dict[str, Any]]:
        base = self._require_base()
        params: Dict[str, str] = {"date": date_str}
        if competition_code:
            params["competition_code"] = competition_code
        url = f"{base}/v1/matches"
        data = self._get_json(url, params=params)

        if isinstance(data, list):
            return list(data)

        matches = data.get("matches") or data.get("items") or data.get("results")
        if isinstance(matches, list):
            return [m if isinstance(m, dict) else {"raw": m} for m in matches]

        if "meta" in data or "schema_version" in data:
            return [data]

        logger.warning("OpenClaw list response had no recognizable list key: keys=%s", list(data.keys())[:12])
        return []

    def fetch_match_payload(self, openclaw_event_id: str) -> OpenClawMatchPayload:
        return OpenClawMatchPayload.model_validate(self._unwrap_payload(self.fetch_match_raw(openclaw_event_id)))

    def fetch_matches_payloads_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[OpenClawMatchPayload]:
        out: List[OpenClawMatchPayload] = []
        for blob in self.fetch_matches_for_date_raw(date_str, competition_code):
            try:
                out.append(OpenClawMatchPayload.model_validate(self._unwrap_payload(blob)))
            except Exception as e:
                logger.warning("Skipping invalid OpenClaw match blob: %s", e)
        return out

    def fetch_match_payload_from_dict(self, blob: Dict[str, Any]) -> OpenClawMatchPayload:
        """For tests / queue consumers that already deserialized JSON."""

        return OpenClawMatchPayload.model_validate(self._unwrap_payload(blob))

    def _get_json(self, url: str, params: Optional[Dict[str, str]] = None) -> Any:
        try:
            resp = self._session.get(url, params=params or {}, timeout=self._timeout)
        except requests.RequestException as e:
            raise OpenClawClientError(f"OpenClaw request failed: {url}: {e}") from e

        if resp.status_code >= 400:
            raise OpenClawClientError(
                f"OpenClaw HTTP {resp.status_code} for {url}: {resp.text[:400]}",
            )
        try:
            return resp.json()
        except ValueError as e:
            raise OpenClawClientError(f"Invalid JSON from {url}") from e

    @staticmethod
    def _unwrap_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("payload", "data", "match", "snapshot_input"):
            inner = data.get(key)
            if isinstance(inner, dict):
                return inner
        return data
