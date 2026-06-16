"""
Brave Search → structured coach/news context orchestration.

Enrichment-only: never overrides Flashscore factual identity fields.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from football_agent import config
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.news_context.extraction import extract_coach_block, extract_general_news_block, hits_to_sources
from football_agent.news_context.models import MatchNewsContext
from football_agent.news_context.query_builder import NewsSearchQuery, build_match_news_queries
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.services.brave_search_client import (
    BraveSearchClient,
    BraveSearchHit,
    BraveSearchUnavailableError,
    filter_hits_by_lookback,
)

logger = logging.getLogger(__name__)


def _brave_master_enabled() -> bool:
    return bool(config.USE_BRAVE_NEWS_ENRICHMENT)


def _brave_legacy_enabled() -> bool:
    return bool(config.USE_OPENCLAW_NEWS or config.USE_OPENCLAW_COACH_CONTEXT)


def brave_news_enabled() -> bool:
    """
    True when Brave Search enrichment should run.

    Master flag: ``USE_BRAVE_NEWS_ENRICHMENT`` (+ API key).
    Legacy aliases: ``USE_OPENCLAW_NEWS`` / ``USE_OPENCLAW_COACH_CONTEXT``.
    """
    if not config.BRAVE_SEARCH_API_KEY:
        return False
    return _brave_master_enabled() or _brave_legacy_enabled()


def brave_coach_context_enabled() -> bool:
    """Coach block/queries — on by default when master Brave flag is set."""
    if _brave_master_enabled():
        return True
    return bool(config.USE_OPENCLAW_COACH_CONTEXT)


def brave_general_news_enabled() -> bool:
    """General news block/queries — on by default when master Brave flag is set."""
    if _brave_master_enabled():
        return True
    return bool(config.USE_OPENCLAW_NEWS)


def _coach_hints_from_openclaw(ctx: Optional[OpenClawMatchContext]) -> tuple[Optional[str], Optional[str]]:
    if ctx is None or ctx.coach_context is None:
        return None, None
    cc = ctx.coach_context
    return cc.home.coach_name, cc.away.coach_name


def _coach_hints_from_facts(facts: FlashscoreMatchFacts) -> tuple[Optional[str], Optional[str]]:
    sq = facts.squad_raw
    if sq is None:
        return None, None
    return sq.coach_name_home, sq.coach_name_away


def enrich_match_news_from_brave(
    facts: FlashscoreMatchFacts,
    *,
    openclaw_context: Optional[OpenClawMatchContext] = None,
    client: Optional[BraveSearchClient] = None,
) -> Optional[MatchNewsContext]:
    """
    Fetch Brave articles and build structured coach + general news blocks.

    Returns None when disabled/unconfigured. Never raises when OPENCLAW_FAIL_SOFT=true.
    """
    if not brave_news_enabled():
        return None

    home = facts.meta.home_team_name
    away = facts.meta.away_team_name
    now = datetime.now(timezone.utc)

    home_coach, away_coach = _coach_hints_from_openclaw(openclaw_context)
    if not home_coach and not away_coach:
        home_coach, away_coach = _coach_hints_from_facts(facts)

    coach_on = brave_coach_context_enabled()
    queries = build_match_news_queries(
        home_team=home,
        away_team=away,
        home_coach_name=home_coach if coach_on else None,
        away_coach_name=away_coach if coach_on else None,
        include_coach_terms=config.BRAVE_NEWS_INCLUDE_COACH_TERMS and coach_on,
        include_injury_terms=config.BRAVE_NEWS_INCLUDE_INJURY_TERMS,
        include_lineup_terms=config.BRAVE_NEWS_INCLUDE_LINEUP_TERMS,
    )

    brave = client or BraveSearchClient()
    if not brave.configured:
        return None

    all_hits: List[BraveSearchHit] = []
    warnings: List[str] = []

    try:
        # Pass 1: team-focused queries
        for nq in queries:
            if nq.category == "coach" and not coach_on:
                continue
            try:
                hits = brave.search(
                    nq.query,
                    count=min(3, config.BRAVE_SEARCH_MAX_RESULTS),
                    freshness_hours=config.BRAVE_NEWS_LOOKBACK_HOURS,
                    topic_tag=nq.category,
                )
                all_hits.extend(hits)
            except BraveSearchUnavailableError as exc:
                warnings.append(f"brave_query_failed:{nq.category}")
                logger.debug("Brave query failed %s: %s", nq.query, exc)
                if not config.OPENCLAW_FAIL_SOFT:
                    raise

        # Pass 2: coach-specific if names discovered in pass-1 snippets
        if coach_on:
            discovered_home, discovered_away = home_coach, away_coach
            if not discovered_home or not discovered_away:
                from football_agent.news_context.extraction import extract_coach_block as _peek

                peek = _peek(
                    hits=all_hits[:10],
                    home_team=home,
                    away_team=away,
                    home_coach_hint=home_coach,
                    away_coach_hint=away_coach,
                )
                discovered_home = discovered_home or peek.home_coach_name
                discovered_away = discovered_away or peek.away_coach_name
            coach_queries = build_match_news_queries(
                home_team=home,
                away_team=away,
                home_coach_name=discovered_home,
                away_coach_name=discovered_away,
                include_injury_terms=False,
                include_lineup_terms=False,
            )
            for nq in [q for q in coach_queries if q.category in ("coach", "h2h")]:
                try:
                    hits = brave.search(
                        nq.query,
                        freshness_hours=config.BRAVE_COACH_H2H_LOOKBACK_DAYS * 24,
                        topic_tag=nq.category,
                    )
                    all_hits.extend(hits)
                except BraveSearchUnavailableError:
                    warnings.append(f"brave_coach_pass_failed:{nq.category}")

    except BraveSearchUnavailableError as exc:
        if config.OPENCLAW_FAIL_SOFT:
            warnings.append(f"brave_search_unavailable:{exc}")
            return MatchNewsContext(
                match_id=facts.meta.match_id,
                home_team=home,
                away_team=away,
                collected_at_utc=now,
                warnings=warnings,
                missing_fields=["all"],
            )
        raise

    # Dedupe by URL/title
    seen = set()
    deduped: List[BraveSearchHit] = []
    for h in all_hits:
        key = (h.url or h.title).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)

    deduped = deduped[: config.BRAVE_NEWS_MAX_ARTICLES_PER_MATCH]
    deduped = filter_hits_by_lookback(
        deduped,
        lookback_hours=config.BRAVE_NEWS_LOOKBACK_HOURS,
        home_team=home,
        away_team=away,
    )

    coach_block = extract_coach_block(
        hits=deduped,
        home_team=home,
        away_team=away,
        home_coach_hint=home_coach,
        away_coach_hint=away_coach,
    ) if coach_on else coach_block_empty()

    general_block = extract_general_news_block(
        hits=deduped,
        home_team=home,
        away_team=away,
    ) if brave_general_news_enabled() else general_block_empty()

    freshest = max((h.published_at for h in deduped if h.published_at), default=None)
    stale_cutoff = now - timedelta(hours=config.BRAVE_NEWS_LOOKBACK_HOURS)
    is_stale = freshest is not None and freshest < stale_cutoff
    freshness_status = "fresh" if deduped and not is_stale else ("stale" if deduped else "unknown")

    confidence = max(coach_block.coach_news_confidence, general_block.general_news_confidence)
    if not deduped:
        confidence = 0.0
        warnings.append("brave_news_no_results")

    return MatchNewsContext(
        match_id=facts.meta.match_id,
        home_team=home,
        away_team=away,
        coach=coach_block,
        general_news=general_block,
        sources=hits_to_sources(deduped),
        collected_at_utc=now,
        freshest_source_at_utc=freshest,
        source_count=len(deduped),
        is_stale=is_stale,
        freshness_status=freshness_status,
        confidence=confidence,
        warnings=warnings,
    )


def coach_block_empty():
    from football_agent.news_context.models import CoachContextBlock

    return CoachContextBlock(warnings=["coach_context_skipped"])


def general_block_empty():
    from football_agent.news_context.models import GeneralNewsBlock

    return GeneralNewsBlock(warnings=["general_news_skipped"])
