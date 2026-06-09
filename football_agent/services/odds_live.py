"""
Live odds fetch for runtime pipelines (Telegram, long-lived workers).

Graceful degradation: never raises to callers — returns status + warnings instead.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from football_agent import config
from football_agent.services.enrichment_config import resolve_enrichment_routing
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.adapters.errors import OddsServiceError, OddsServiceUnavailableError
from football_agent.odds.adapters.http_backend import HttpOddsAdapter
from football_agent.odds.models import MatchOddsContext
from football_agent.odds.service import OddsIngestionService
from football_agent.services.http_fetch_result import classify_http_error_message

logger = logging.getLogger(__name__)


def resolve_odds_service_url(
    override: Optional[str] = None,
    *,
    skip: bool = False,
) -> Optional[str]:
    if skip:
        return None
    routing = resolve_enrichment_routing(odds_url_override=override, skip_odds=False)
    return routing.odds_base_url


def fetch_odds_for_facts(
    facts: FlashscoreMatchFacts,
    *,
    odds_url: Optional[str] = None,
    api_key: Optional[str] = None,
    skip: bool = False,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
) -> Tuple[Optional[MatchOddsContext], Dict[str, str], List[str]]:
    """
    Fetch live odds for a Flashscore match.

    Returns ``(odds_context_or_none, sources, warnings)``.

    Never raises — unavailable service → ``odds=failed/skipped`` + warnings.
    """
    warnings: List[str] = []
    service_url = resolve_odds_service_url(odds_url, skip=skip)
    if not service_url:
        return None, {"odds": "skipped"}, warnings

    home = home_override or facts.meta.home_team_name
    away = away_override or facts.meta.away_team_name
    kickoff = facts.meta.kickoff_utc.isoformat() if facts.meta.kickoff_utc else None
    date_str = date_override
    if not date_str and facts.meta.kickoff_utc:
        date_str = facts.meta.kickoff_utc.date().isoformat()

    token = HttpOddsAdapter.build_query_token(
        home=home,
        away=away,
        date=date_str,
        competition=competition_override,
        competition_name=facts.meta.competition_name,
        kickoff_utc=kickoff,
        match_id=facts.meta.match_id,
        match_url=match_url_override or facts.meta.source_url,
    )

    try:
        adapter = HttpOddsAdapter(
            service_url,
            api_key=api_key or config.ODDS_SERVICE_API_KEY,
            timeout_s=config.ODDS_SERVICE_TIMEOUT_S,
        )
        ctx = OddsIngestionService(adapter).get_odds_for_fixture(token)
        if ctx is None:
            warnings.append("odds_empty_response")
            return None, {"odds": "failed"}, warnings
        return ctx, {"odds": "ok"}, warnings
    except (OddsServiceUnavailableError, OddsServiceError) as exc:
        msg = str(exc)
        reason = classify_http_error_message(msg)
        warnings.append(f"odds_fetch_failed:{reason}")
        warnings.append(f"odds_detail: {msg[:120]}")
        logger.warning("Odds fetch failed reason=%s (continuing without book line): %s", reason, msg)
        return None, {"odds": "failed"}, warnings
