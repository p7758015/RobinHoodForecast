"""
Brave Search → structured coach/news context orchestration.

Enrichment-only: never overrides Flashscore factual identity fields.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from football_agent import config
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.news_context.coach_sync import sync_coach_context_block
from football_agent.news_context.extraction import extract_coach_block, extract_general_news_block, hits_to_sources
from football_agent.news_context.models import MatchNewsContext
from football_agent.news_context.query_builder import NewsSearchQuery, build_match_news_queries
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.services.brave_search_client import (
    BraveSearchClient,
    BraveSearchHit,
    BraveSearchUnavailableError,
    filter_hits_by_lookback,
    rank_and_cap_hits,
)
from football_agent.services.brave_news_cache import BraveNewsCache
from football_agent.storage.match_key import build_match_key

logger = logging.getLogger(__name__)

BraveEnrichmentOutcome = Literal[
    "disabled",
    "skipped_not_configured",
    "no_results",
    "api_error",
    "parsing_error",
    "low_confidence_results",
    "success_partial",
    "success_useful",
]


def classify_match_news_enrichment_status(
    news: Optional[MatchNewsContext],
    *,
    error: Optional[str] = None,
    enabled: bool = True,
) -> BraveEnrichmentOutcome:
    """Classify Brave enrichment outcome for trace / diagnostics (fail-soft)."""
    if not enabled:
        return "disabled"
    if error:
        err = error.lower()
        if "unavailable" in err or "http" in err or "api" in err or "query_failed" in err:
            return "api_error"
        return "parsing_error"
    if news is None:
        return "skipped_not_configured"
    warnings_l = [w.lower() for w in (news.warnings or [])]
    if news.source_count == 0 and any(
        "unavailable" in w or "query_failed" in w for w in warnings_l
    ):
        return "api_error"
    if news.source_count == 0:
        return "no_results"
    useful = bool(
        news.coach.home_coach_name
        or news.coach.away_coach_name
        or news.general_news.injuries_signals
        or news.general_news.suspension_signals
        or news.general_news.predicted_lineup_signals
        or news.general_news.locker_room_signals
        or news.general_news.motivation_signals
    )
    if useful and (news.confidence or 0) >= 0.35:
        return "success_useful"
    if news.source_count > 0 and (news.confidence or 0) < 0.25:
        return "low_confidence_results"
    if news.source_count > 0:
        return "success_partial"
    return "no_results"


def summarize_brave_news_context(news: Optional[MatchNewsContext]) -> dict:
    """Compact structured summary for debug traces."""
    if news is None:
        return {}
    gn = news.general_news
    return {
        "coach_home": news.coach.home_coach_name,
        "coach_away": news.coach.away_coach_name,
        "coach_home_confidence": news.coach.news.home_coach_confidence,
        "coach_away_confidence": news.coach.news.away_coach_confidence,
        "coach_confidence": news.coach.coach_news_confidence,
        "injuries": list(gn.injuries_signals or [])[:5],
        "home_injuries": list(gn.home_injuries_signals or [])[:5],
        "away_injuries": list(gn.away_injuries_signals or [])[:5],
        "suspensions": list(gn.suspension_signals or [])[:5],
        "home_suspensions": list(gn.home_suspension_signals or [])[:5],
        "away_suspensions": list(gn.away_suspension_signals or [])[:5],
        "lineup_signals": list(gn.predicted_lineup_signals or [])[:5],
        "locker_room": list(gn.locker_room_signals or [])[:3],
        "motivation": list(gn.motivation_signals or [])[:3],
        "home_motivation": list(gn.home_motivation_signals or [])[:3],
        "away_motivation": list(gn.away_motivation_signals or [])[:3],
        "unassigned": list(gn.unassigned_signals or [])[:3],
        "rotation_home": news.coach.home_coach_rotation_signal,
        "rotation_away": news.coach.away_coach_rotation_signal,
        "profile_home_teams": (news.coach.profile_home.previous_teams[:3] if news.coach.profile_home else []),
        "profile_away_teams": (news.coach.profile_away.previous_teams[:3] if news.coach.profile_away else []),
        "profile_away_strength": (
            news.coach.profile_away.coach_global_strength_score if news.coach.profile_away else None
        ),
        "source_count": news.source_count,
        "confidence": news.confidence,
        "freshness": news.freshness_status,
        "warnings": list(news.warnings or [])[:8],
    }


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
    match_key = build_match_key(
        competition=facts.meta.competition_name or "",
        kickoff_utc=facts.meta.kickoff_utc,
        home_team=home,
        away_team=away,
    )

    cache = BraveNewsCache()
    cached = cache.get(match_key)
    if cached is not None:
        return cached.model_copy(update={"warnings": list(cached.warnings or []) + ["brave_news_cache_hit"]})

    home_coach, away_coach = _coach_hints_from_openclaw(openclaw_context)
    if not home_coach and not away_coach:
        home_coach, away_coach = _coach_hints_from_facts(facts)

    coach_on = brave_coach_context_enabled()
    queries = build_match_news_queries(
        home_team=home,
        away_team=away,
        home_coach_name=home_coach if coach_on else None,
        away_coach_name=away_coach if coach_on else None,
        competition_name=facts.meta.competition_name,
        competition_country=facts.meta.competition_country,
        include_coach_terms=config.BRAVE_NEWS_INCLUDE_COACH_TERMS and coach_on,
        include_injury_terms=config.BRAVE_NEWS_INCLUDE_INJURY_TERMS,
        include_lineup_terms=config.BRAVE_NEWS_INCLUDE_LINEUP_TERMS,
    )

    brave = client or BraveSearchClient()
    if not brave.configured:
        return None

    country_code: Optional[str] = None
    search_lang: Optional[str] = None
    cc = (facts.meta.competition_country or "").strip().lower()
    if cc in ("brazil", "brasil"):
        country_code = "BR"
        search_lang = "pt-br"
    elif cc in ("finland",):
        country_code = "FI"
        search_lang = "fi"

    all_hits: List[BraveSearchHit] = []
    warnings: List[str] = []

    try:
        # Pass 1: team-focused queries
        # Injury / desfalques queries first — often carry coach + squad signals for Serie B
        injury_queries = sorted(
            [q for q in queries if q.category in ("injuries", "lineup")],
            key=lambda q: (q.category, q.query.lower()),
        )
        other_queries = sorted(
            [q for q in queries if q.category not in ("injuries", "lineup")],
            key=lambda q: (q.category, q.query.lower()),
        )
        ordered_queries = injury_queries + other_queries

        for nq in ordered_queries:
            if nq.category == "coach" and not coach_on:
                continue
            try:
                hits = brave.search(
                    nq.query,
                    count=min(3, config.BRAVE_SEARCH_MAX_RESULTS),
                    freshness_hours=config.BRAVE_NEWS_LOOKBACK_HOURS,
                    topic_tag=nq.category,
                    country=country_code,
                    search_lang=search_lang,
                )
                all_hits.extend(hits)
            except BraveSearchUnavailableError as exc:
                warnings.append(f"brave_query_failed:{nq.category}:{exc}")
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
                competition_name=facts.meta.competition_name,
                competition_country=facts.meta.competition_country,
                include_injury_terms=False,
                include_lineup_terms=False,
            )
            for nq in [q for q in coach_queries if q.category in ("coach", "h2h")]:
                try:
                    hits = brave.search(
                        nq.query,
                        freshness_hours=config.BRAVE_COACH_H2H_LOOKBACK_DAYS * 24,
                        topic_tag=nq.category,
                        country=country_code,
                        search_lang=search_lang,
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

    deduped = filter_hits_by_lookback(
        deduped,
        lookback_hours=config.BRAVE_NEWS_LOOKBACK_HOURS,
        home_team=home,
        away_team=away,
        competition_country=facts.meta.competition_country,
    )
    deduped = rank_and_cap_hits(deduped, max_count=config.BRAVE_NEWS_MAX_ARTICLES_PER_MATCH)

    coach_block = extract_coach_block(
        hits=deduped,
        home_team=home,
        away_team=away,
        home_coach_hint=home_coach,
        away_coach_hint=away_coach,
        competition_country=facts.meta.competition_country,
    ) if coach_on else coach_block_empty()

    if coach_on and coach_block.news.home_coach_name:
        coach_block = _attach_coach_profile(
            coach_block,
            side="home",
            coach_name=coach_block.news.home_coach_name,
            brave=brave,
            country_code=country_code,
            search_lang=search_lang,
            warnings=warnings,
        )
    if coach_on and coach_block.news.away_coach_name:
        coach_block = _attach_coach_profile(
            coach_block,
            side="away",
            coach_name=coach_block.news.away_coach_name,
            brave=brave,
            country_code=country_code,
            search_lang=search_lang,
            warnings=warnings,
        )

    general_block = extract_general_news_block(
        hits=deduped,
        home_team=home,
        away_team=away,
        competition_country=facts.meta.competition_country,
        home_coach_hint=coach_block.news.home_coach_name,
        away_coach_hint=coach_block.news.away_coach_name,
    ) if brave_general_news_enabled() else general_block_empty()

    freshest = max((h.published_at for h in deduped if h.published_at), default=None)
    stale_cutoff = now - timedelta(hours=config.BRAVE_NEWS_LOOKBACK_HOURS)
    is_stale = freshest is not None and freshest < stale_cutoff
    freshness_status = "fresh" if deduped and not is_stale else ("stale" if deduped else "unknown")

    confidence = max(coach_block.coach_news_confidence, general_block.general_news_confidence)
    if not deduped:
        confidence = 0.0
        warnings.append("brave_news_no_results")

    ctx = MatchNewsContext(
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
    outcome = classify_match_news_enrichment_status(ctx, enabled=True)
    if outcome not in ("success_useful", "success_partial") and not any(
        w.startswith("brave_enrichment_status:") for w in ctx.warnings
    ):
        ctx = ctx.model_copy(update={"warnings": list(ctx.warnings) + [f"brave_enrichment_status:{outcome}"]})
    cache.put(match_key, ctx)
    return ctx


def coach_block_empty():
    from football_agent.news_context.models import CoachContextBlock

    return sync_coach_context_block(CoachContextBlock(warnings=["coach_context_skipped"]))


def _attach_coach_profile(
    coach_block,
    *,
    side: str,
    coach_name: str,
    brave: BraveSearchClient,
    country_code: Optional[str],
    search_lang: Optional[str],
    warnings: List[str],
):
    from football_agent.news_context.coach_profile import extract_coach_profile_block
    from football_agent.news_context.coach_sync import sync_coach_context_block
    from football_agent.news_context.query_builder import build_coach_profile_queries

    profile_hits: List[BraveSearchHit] = []
    for nq in build_coach_profile_queries(
        coach_name,
        competition_country=country_code,
    ):
        try:
            profile_hits.extend(
                brave.search(
                    nq.query,
                    count=2,
                    freshness_hours=config.BRAVE_COACH_HISTORY_LOOKBACK_DAYS * 24,
                    topic_tag=nq.category,
                    country=country_code,
                    search_lang=search_lang,
                ),
            )
        except BraveSearchUnavailableError:
            warnings.append(f"brave_profile_failed:{side}")

    profile = extract_coach_profile_block(coach_name=coach_name, hits=profile_hits[:6])
    if side == "home":
        return sync_coach_context_block(coach_block.model_copy(update={"profile_home": profile}))
    return sync_coach_context_block(coach_block.model_copy(update={"profile_away": profile}))


def general_block_empty():
    from football_agent.news_context.models import GeneralNewsBlock

    return GeneralNewsBlock(warnings=["general_news_skipped"])
