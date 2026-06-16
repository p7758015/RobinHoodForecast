"""
Optional Brave-assisted query normalization for competition discovery.

Brave is used ONLY to normalize free-text queries (e.g. RU phrases → English
league names). Fixtures truth remains Flashscore scraper.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from football_agent import config
from football_agent.discovery.aliases import lookup_static_alias

logger = logging.getLogger(__name__)


def brave_normalization_enabled() -> bool:
    return bool(
        config.USE_BRAVE_NEWS_ENRICHMENT
        and config.BRAVE_SEARCH_API_KEY
        and config.DISCOVERY_BRAVE_NORMALIZE
    )


def normalize_competition_query(query: str, *, brave_client=None) -> Optional[str]:
    """
    Return a cleaner English search phrase for Flashscore discovery.

    Order: static aliases → optional Brave web search snippet parse.
    """
    static = lookup_static_alias(query)
    if static:
        name, country = static
        return f"{name} {country}" if country else name

    if not brave_normalization_enabled():
        return None

    try:
        from football_agent.services.brave_search_client import BraveSearchClient

        client = brave_client or BraveSearchClient()
        if not client.configured:
            return None
        hits = client.search(
            f"{query.strip()} football league flashscore",
            count=3,
            freshness_hours=24 * 30,
        )
        for hit in hits:
            title = (hit.title or "").strip()
            if not title:
                continue
            cleaned = _extract_league_name_from_title(title)
            if cleaned:
                logger.info("Brave normalized competition query %r -> %r", query, cleaned)
                return cleaned
    except Exception as exc:
        logger.warning("Brave competition normalization failed: %s", exc)
    return None


def _extract_league_name_from_title(title: str) -> Optional[str]:
    """Heuristic: pick league-like segment from search result title."""
    text = re.sub(r"\s*[\|\-–—]\s*.*$", "", title).strip()
    text = re.sub(r"(?i)flashscore.*$", "", text).strip()
    if len(text) < 4:
        return None
    if re.search(r"(?i)flashscore|livescore|sofascore|results|table|standings", text):
        return None
    return text
