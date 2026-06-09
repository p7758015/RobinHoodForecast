"""Shared HTTP helpers for live scraper/context adapters (debug/CLI only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


def apply_api_key(session: requests.Session, api_key: Optional[str]) -> None:
    if api_key:
        session.headers.setdefault("Authorization", f"Bearer {api_key}")


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, str]] = None,
    timeout_s: float,
    error_cls: type[Exception],
) -> Any:
    try:
        resp = session.get(url, params=params or {}, timeout=timeout_s)
    except requests.RequestException as e:
        raise error_cls(f"Request failed: {url}: {e}") from e

    if resp.status_code >= 400:
        raise error_cls(
            f"HTTP {resp.status_code} for {url}: {resp.text[:400]}",
        )
    try:
        return resp.json()
    except ValueError as e:
        raise error_cls(f"Invalid JSON from {url}") from e


def unwrap_dict_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("payload", "data", "match", "context", "result"):
        inner = data.get(key)
        if isinstance(inner, dict):
            return inner
    return data


def normalize_match_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("matches", "items", "results", "data"):
        block = data.get(key)
        if isinstance(block, list):
            return [item for item in block if isinstance(item, dict)]
    if any(k in data for k in ("match_id", "home_team_name", "home", "source_url")):
        return [data]
    return []
