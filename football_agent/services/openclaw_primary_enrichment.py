"""
OpenClaw-primary enrichment routing helpers.

When ``OPENCLAW_PRIMARY_ENRICHMENT`` is true (default for v2), OpenClaw context blocks
are the primary source for squad/coach/news/scorer signals. Brave is optional fallback only.
"""

from __future__ import annotations

from football_agent import config


def openclaw_primary_enrichment() -> bool:
    """True when OpenClaw (not Brave) owns scorer-critical enrichment blocks."""
    return bool(config.OPENCLAW_PRIMARY_ENRICHMENT)


def brave_fallback_allowed() -> bool:
    """
    Brave may run only when explicitly enabled as fallback.

    ``USE_BRAVE_NEWS_ENRICHMENT`` alone enables Brave even under primary mode.
    ``USE_BRAVE_NEWS_FALLBACK`` is an alias for explicit opt-in fallback.
    """
    if not config.BRAVE_SEARCH_API_KEY:
        return False
    if config.USE_BRAVE_NEWS_ENRICHMENT or config.USE_BRAVE_NEWS_FALLBACK:
        return True
    if openclaw_primary_enrichment():
        return False
    return bool(config.USE_OPENCLAW_NEWS or config.USE_OPENCLAW_COACH_CONTEXT)


def openclaw_live_context_enabled() -> bool:
    """OpenClaw context fetch should run for live enrichment."""
    if not config.USE_OPENCLAW:
        return False
    if openclaw_primary_enrichment():
        return True
    return bool(config.USE_OPENCLAW_LIVE_CONTEXT)


def openclaw_news_block_enabled() -> bool:
    """News/context signals should be derived from OpenClaw blocks."""
    if not config.USE_OPENCLAW:
        return False
    if openclaw_primary_enrichment():
        return True
    return bool(config.USE_OPENCLAW_NEWS)


def openclaw_coach_block_enabled() -> bool:
    if not config.USE_OPENCLAW:
        return False
    if openclaw_primary_enrichment():
        return True
    return bool(config.USE_OPENCLAW_COACH_CONTEXT)
