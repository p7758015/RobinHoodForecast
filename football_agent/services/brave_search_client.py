"""Thin Brave Search API client (fail-soft, enrichment-only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

from football_agent import config
from football_agent.news_context.source_reliability import hit_rank_key
from football_agent.news_context.team_scope import build_team_scope

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RETRIES = 2

# Brave API rejects bare ISO-639-1 codes for some locales (e.g. pt → use pt-br).
_BRAVE_SEARCH_LANG_ALIASES = {
    "pt": "pt-br",
    "pt_br": "pt-br",
    "pt-pt": "pt-pt",
    "zh": "zh-hans",
    "ja": "jp",
}


def normalize_brave_search_lang(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lang = value.strip().lower().replace("_", "-")
    return _BRAVE_SEARCH_LANG_ALIASES.get(lang, lang)


class BraveSearchError(Exception):
    pass


class BraveSearchUnavailableError(BraveSearchError):
    pass


@dataclass
class BraveSearchHit:
    title: str
    url: Optional[str] = None
    description: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    topic_tags: List[str] = field(default_factory=list)


class BraveSearchClient:
    """Brave web search with subscription token auth."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_results: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = (api_key or config.BRAVE_SEARCH_API_KEY or "").strip()
        self._base_url = (base_url or config.BRAVE_SEARCH_BASE_URL or DEFAULT_BASE_URL).strip()
        self._timeout = timeout_s or config.BRAVE_SEARCH_TIMEOUT_S
        self._max_results = max_results or config.BRAVE_SEARCH_MAX_RESULTS
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def search(
        self,
        query: str,
        *,
        count: Optional[int] = None,
        freshness_hours: Optional[int] = None,
        topic_tag: Optional[str] = None,
        country: Optional[str] = None,
        search_lang: Optional[str] = None,
    ) -> List[BraveSearchHit]:
        if not self._api_key:
            raise BraveSearchUnavailableError("BRAVE_SEARCH_API_KEY is not set")
        q = (query or "").strip()
        if not q:
            return []

        params: Dict[str, Any] = {
            "q": q,
            "count": min(count or self._max_results, self._max_results),
        }
        if freshness_hours and freshness_hours > 0:
            # Brave freshness: pd = past day, pw = past week — approximate via lookback
            if freshness_hours <= 24:
                params["freshness"] = "pd"
            elif freshness_hours <= 168:
                params["freshness"] = "pw"
            else:
                params["freshness"] = "pm"
        if country:
            params["country"] = country.strip().upper()
        if search_lang:
            normalized = normalize_brave_search_lang(search_lang)
            if normalized:
                params["search_lang"] = normalized

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.get(
                    self._base_url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
                if resp.status_code >= 400:
                    raise BraveSearchUnavailableError(
                        f"Brave HTTP {resp.status_code}: {resp.text[:300]}",
                    )
                try:
                    data = resp.json()
                except ValueError as exc:
                    ctype = (resp.headers.get("content-type") or "").lower()
                    body = (resp.text or "").strip()
                    raise BraveSearchUnavailableError(
                        f"Brave non-JSON HTTP {resp.status_code} "
                        f"content-type={ctype or 'unknown'} body={body[:160]!r}: {exc}",
                    ) from exc
                return self._parse_results(data, topic_tag=topic_tag)
            except (requests.RequestException, BraveSearchUnavailableError, BraveSearchError) as exc:
                last_exc = exc
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
        raise BraveSearchUnavailableError(str(last_exc or "Brave search failed"))

    def _parse_results(self, data: dict, *, topic_tag: Optional[str]) -> List[BraveSearchHit]:
        web = data.get("web") if isinstance(data, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            return []

        hits: List[BraveSearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            url = item.get("url")
            desc = item.get("description") or item.get("snippet")
            age = item.get("page_age") or item.get("age")
            published = _parse_age(age)
            source = None
            if isinstance(url, str) and "://" in url:
                try:
                    source = url.split("/")[2]
                except IndexError:
                    source = None
            tags = [topic_tag] if topic_tag else []
            hits.append(
                BraveSearchHit(
                    title=title,
                    url=str(url) if url else None,
                    description=str(desc) if desc else None,
                    source_name=source,
                    published_at=published,
                    topic_tags=tags,
                ),
            )
        return hits


def _parse_age(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    low = text.lower()
    now = datetime.now(timezone.utc)
    if "hour" in low:
        try:
            n = int("".join(c for c in low.split("hour")[0].split()[-1] if c.isdigit()) or "1")
            return now - timedelta(hours=n)
        except ValueError:
            return now - timedelta(hours=12)
    if "day" in low:
        try:
            n = int("".join(c for c in low.split("day")[0].split()[-1] if c.isdigit()) or "1")
            return now - timedelta(days=n)
        except ValueError:
            return now - timedelta(days=1)
    return None


def filter_hits_by_lookback(
    hits: List[BraveSearchHit],
    *,
    lookback_hours: int,
    home_team: str,
    away_team: str,
    competition_country: Optional[str] = None,
) -> List[BraveSearchHit]:
    """Drop stale or obviously irrelevant hits (fail-soft)."""
    if not hits:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    scope = build_team_scope(home_team, away_team, competition_country=competition_country)

    filtered: List[BraveSearchHit] = []
    for h in hits:
        text = f"{h.title} {h.description or ''}"
        from football_agent.news_context.team_scope import classify_ownership

        own = classify_ownership(text, scope)
        if own.side == "unassigned":
            relaxed = {"coach", "h2h", "preview", "injuries", "lineup", "rotation"}
            if not any(t in (h.topic_tags or []) for t in relaxed):
                continue
            continue
        if h.published_at and h.published_at < cutoff:
            continue
        filtered.append(h)
    return filtered


def rank_and_cap_hits(hits: List[BraveSearchHit], *, max_count: int) -> List[BraveSearchHit]:
    """Deterministic ordering: trusted sources + injury/coach tags survive cap."""
    ordered = sorted(
        hits,
        key=lambda h: hit_rank_key(
            url=h.url,
            source_name=h.source_name,
            title=h.title,
            topic_tag=(h.topic_tags[0] if h.topic_tags else None),
        ),
    )
    return ordered[:max_count]
