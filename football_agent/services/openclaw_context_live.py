"""
Live OpenClaw context fetch for runtime pipelines (Telegram, long-lived workers).

Graceful degradation: never raises to callers — returns status + warnings instead.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from football_agent import config
from football_agent.services.enrichment_config import resolve_openclaw_base_url
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.openclaw_context.adapters.errors import (
    OpenClawContextError,
    OpenClawContextUnavailableError,
)
from football_agent.openclaw_context.adapters.http_backend import HttpOpenClawContextAdapter
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.services.http_fetch_result import classify_http_error_message

logger = logging.getLogger(__name__)


def resolve_openclaw_context_url(
    override: Optional[str] = None,
    *,
    skip: bool = False,
) -> Optional[str]:
    if skip:
        return None
    return resolve_openclaw_base_url(override)


def fetch_openclaw_context_for_facts(
    facts: FlashscoreMatchFacts,
    *,
    openclaw_url: Optional[str] = None,
    api_key: Optional[str] = None,
    skip: bool = False,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
) -> Tuple[Optional[OpenClawMatchContext], Dict[str, str], List[str]]:
    """
    Fetch OpenClaw context for a Flashscore match.

    Returns ``(context_or_none, sources, warnings)``.

    Never raises — unavailable service → ``openclaw=failed/skipped`` + warnings.
    """
    warnings: List[str] = []
    oc_url = resolve_openclaw_context_url(openclaw_url, skip=skip)
    if not oc_url:
        return None, {"openclaw": "skipped"}, warnings

    home = home_override or facts.meta.home_team_name
    away = away_override or facts.meta.away_team_name
    kickoff = facts.meta.kickoff_utc.isoformat() if facts.meta.kickoff_utc else None
    date_str = date_override
    if not date_str and facts.meta.kickoff_utc:
        date_str = facts.meta.kickoff_utc.date().isoformat()

    token = HttpOpenClawContextAdapter.build_query_token(
        home=home,
        away=away,
        date=date_str,
        competition=competition_override,
        competition_name=facts.meta.competition_name,
        kickoff_utc=kickoff,
    )

    try:
        adapter = HttpOpenClawContextAdapter(
            oc_url,
            api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
            timeout_s=config.OPENCLAW_CONTEXT_TIMEOUT_S,
        )
        ctx = OpenClawContextIngestionService(adapter).get_context_for_fixture(token)
        if ctx is None:
            warnings.append("openclaw_context_empty_response")
            return None, {"openclaw": "failed"}, warnings
        return ctx, {"openclaw": "ok"}, warnings
    except (OpenClawContextUnavailableError, OpenClawContextError) as exc:
        msg = str(exc)
        reason = classify_http_error_message(msg)
        warnings.append(f"openclaw_context_fetch_failed:{reason}")
        warnings.append(f"openclaw_context_detail: {msg[:120]}")
        logger.warning(
            "OpenClaw context fetch failed reason=%s (continuing flashscore-only): %s",
            reason,
            msg,
        )
        return None, {"openclaw": "failed"}, warnings
