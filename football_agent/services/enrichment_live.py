"""
Unified live enrichment fetcher — OpenClaw-first (context + odds).

Orchestrates existing context/odds fetchers; does not pretend a backend exists.
Never raises to pipeline callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from football_agent import config
from football_agent.adapters.http_utils import apply_api_key, get_json, unwrap_dict_payload
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.adapters.errors import OddsServiceError, OddsServiceUnavailableError
from football_agent.odds.adapters.http_backend import HttpOddsAdapter
from football_agent.odds.models import MatchOddsContext
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.errors import (
    OpenClawContextError,
    OpenClawContextUnavailableError,
)
from football_agent.openclaw_context.adapters.http_backend import HttpOpenClawContextAdapter
from football_agent.news_context.models import MatchNewsContext
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.services.enrichment_config import (
    EnrichmentRouting,
    enrichment_uses_bridge,
    resolve_enrichment_routing,
)
from football_agent.services.enrichment_contract import (
    ENRICHMENT_CONTEXT_PATH,
    ENRICHMENT_MODE_UNIFIED,
    ENRICHMENT_ODDS_PATH,
    ENRICHMENT_UNIFIED_PATH,
    ODDS_SOURCE_OPENCLAW,
    SOURCE_FAILED,
    SOURCE_OK,
    SOURCE_PARTIAL,
    SOURCE_SKIPPED,
    SOURCE_SKIPPED_NOT_CONFIGURED,
    parse_unified_enrichment_payload,
)
from football_agent.services.http_fetch_result import classify_http_error_message

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentFetchResult:
    context: Optional[OpenClawMatchContext] = None
    odds: Optional[MatchOddsContext] = None
    news: Optional[MatchNewsContext] = None
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    routing: Optional[EnrichmentRouting] = None

    @property
    def enrichment_mode(self) -> str:
        if self.routing is None:
            return "not_configured"
        return self.routing.enrichment_mode

    @property
    def odds_source(self) -> str:
        if self.routing is None:
            return "none"
        return self.routing.odds_source


def _build_query_token(
    facts: FlashscoreMatchFacts,
    *,
    home_override: Optional[str],
    away_override: Optional[str],
    date_override: Optional[str],
    competition_override: Optional[str],
    match_url_override: Optional[str],
) -> str:
    home = home_override or facts.meta.home_team_name
    away = away_override or facts.meta.away_team_name
    kickoff = facts.meta.kickoff_utc.isoformat() if facts.meta.kickoff_utc else None
    date_str = date_override
    if not date_str and facts.meta.kickoff_utc:
        date_str = facts.meta.kickoff_utc.date().isoformat()
    return HttpOddsAdapter.build_query_token(
        home=home,
        away=away,
        date=date_str,
        competition=competition_override,
        competition_name=facts.meta.competition_name,
        kickoff_utc=kickoff,
        match_id=facts.meta.match_id,
        match_url=match_url_override or facts.meta.source_url,
    )


def _map_context_raw(raw: dict) -> Optional[OpenClawMatchContext]:
    from football_agent.openclaw_context.service import OpenClawContextIngestionService

    class _RawAdapter:
        def fetch_context_raw(self, _token: str) -> dict:
            return raw

    return OpenClawContextIngestionService(_RawAdapter()).get_context_for_fixture("inline")


def _map_odds_raw(raw: dict) -> Optional[MatchOddsContext]:
    class _RawAdapter:
        def fetch_odds_raw(self, _token: str) -> dict:
            return raw

    return OddsIngestionService(_RawAdapter()).get_odds_for_fixture("inline")


def _fetch_context_split(
    routing: EnrichmentRouting,
    token: str,
    api_key: Optional[str],
) -> Tuple[Optional[OpenClawMatchContext], str, List[str]]:
    warnings: List[str] = []
    if not routing.context_base_url:
        return None, SOURCE_SKIPPED_NOT_CONFIGURED, warnings
    try:
        adapter = HttpOpenClawContextAdapter(
            routing.context_base_url,
            api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
            timeout_s=config.OPENCLAW_CONTEXT_TIMEOUT_S,
            context_path=ENRICHMENT_CONTEXT_PATH,
        )
        ctx = OpenClawContextIngestionService(adapter).get_context_for_fixture(token)
        if ctx is None:
            warnings.append("openclaw_context_empty_response")
            return None, SOURCE_FAILED, warnings
        return ctx, SOURCE_OK, warnings
    except (OpenClawContextUnavailableError, OpenClawContextError) as exc:
        reason = classify_http_error_message(str(exc))
        warnings.append(f"openclaw_context_fetch_failed:{reason}")
        warnings.append(f"openclaw_context_detail: {str(exc)[:120]}")
        return None, SOURCE_FAILED, warnings


def _fetch_odds_split(
    routing: EnrichmentRouting,
    token: str,
    api_key: Optional[str],
) -> Tuple[Optional[MatchOddsContext], str, List[str]]:
    warnings: List[str] = []
    if not routing.odds_base_url:
        if routing.odds_source == "none":
            if not routing.openclaw_configured:
                warnings.append("odds_not_configured:no_enrichment_backend")
            elif not routing.openclaw_provides_odds:
                warnings.append("odds_not_configured:openclaw_odds_disabled")
            else:
                warnings.append("odds_not_configured:no_url")
        return None, SOURCE_SKIPPED_NOT_CONFIGURED, warnings

    odds_key = api_key or config.ODDS_SERVICE_API_KEY or config.OPENCLAW_CONTEXT_API_KEY
    try:
        adapter = HttpOddsAdapter(
            routing.odds_base_url,
            api_key=odds_key,
            timeout_s=config.ODDS_SERVICE_TIMEOUT_S,
            odds_path=ENRICHMENT_ODDS_PATH,
        )
        ctx = OddsIngestionService(adapter).get_odds_for_fixture(token)
        if ctx is None:
            warnings.append("odds_empty_response")
            return None, SOURCE_FAILED, warnings
        return ctx, SOURCE_OK, warnings
    except (OddsServiceUnavailableError, OddsServiceError) as exc:
        reason = classify_http_error_message(str(exc))
        warnings.append(f"odds_fetch_failed:{reason}")
        warnings.append(f"odds_detail: {str(exc)[:120]}")
        return None, SOURCE_FAILED, warnings


def _fetch_unified(
    routing: EnrichmentRouting,
    token: str,
    api_key: Optional[str],
) -> Tuple[
    Optional[OpenClawMatchContext],
    Optional[MatchOddsContext],
    Dict[str, str],
    List[str],
]:
    warnings: List[str] = []
    sources: Dict[str, str] = {"openclaw": SOURCE_SKIPPED_NOT_CONFIGURED, "odds": SOURCE_SKIPPED_NOT_CONFIGURED}
    base = routing.openclaw_base_url
    if not base:
        warnings.append("enrichment_unified_not_configured")
        return None, None, sources, warnings

    params = HttpOpenClawContextAdapter._parse_query_token(token)  # type: ignore[attr-defined]
    url = urljoin(base.rstrip("/") + "/", ENRICHMENT_UNIFIED_PATH.lstrip("/"))
    session = requests.Session()
    apply_api_key(session, api_key or config.OPENCLAW_CONTEXT_API_KEY)
    try:
        data = get_json(
            session,
            url,
            params=params,
            timeout_s=config.OPENCLAW_CONTEXT_TIMEOUT_S,
            error_cls=OpenClawContextUnavailableError,
        )
    except (OpenClawContextUnavailableError, OpenClawContextError) as exc:
        reason = classify_http_error_message(str(exc))
        warnings.append(f"enrichment_unified_fetch_failed:{reason}")
        warnings.append(f"enrichment_unified_detail: {str(exc)[:120]}")
        sources["openclaw"] = SOURCE_FAILED
        sources["odds"] = SOURCE_FAILED
        return None, None, sources, warnings

    if not isinstance(data, dict):
        warnings.append("enrichment_unified_bad_payload")
        sources["openclaw"] = SOURCE_FAILED
        sources["odds"] = SOURCE_FAILED
        return None, None, sources, warnings

    raw = unwrap_dict_payload(data)
    ctx_raw, odds_raw, parse_warnings = parse_unified_enrichment_payload(raw or {})
    warnings.extend(parse_warnings)

    ctx: Optional[OpenClawMatchContext] = None
    odds: Optional[MatchOddsContext] = None

    if ctx_raw:
        try:
            ctx = _map_context_raw(ctx_raw)
        except Exception as exc:
            warnings.append(f"enrichment_unified_context_map_failed: {exc}")
    if odds_raw:
        try:
            odds = _map_odds_raw(odds_raw)
        except Exception as exc:
            warnings.append(f"enrichment_unified_odds_map_failed: {exc}")

    oc_status = SOURCE_FAILED
    if ctx is not None:
        oc_status = SOURCE_OK if not parse_warnings else SOURCE_PARTIAL
    elif ctx_raw is None:
        oc_status = SOURCE_FAILED

    odds_status = SOURCE_SKIPPED_NOT_CONFIGURED
    if routing.odds_configured or routing.openclaw_provides_odds:
        if odds is not None:
            odds_status = SOURCE_OK
        elif odds_raw is not None:
            odds_status = SOURCE_FAILED
        else:
            odds_status = SOURCE_FAILED if ctx is not None else SOURCE_SKIPPED_NOT_CONFIGURED

    sources["openclaw"] = oc_status
    sources["odds"] = odds_status
    return ctx, odds, sources, warnings


def fetch_enrichment_for_facts(
    facts: FlashscoreMatchFacts,
    *,
    openclaw_url: Optional[str] = None,
    openclaw_api_key: Optional[str] = None,
    skip_openclaw: bool = False,
    odds_url: Optional[str] = None,
    odds_api_key: Optional[str] = None,
    skip_odds: bool = False,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
    mode_override: Optional[str] = None,
) -> EnrichmentFetchResult:
    """
    Fetch OpenClaw context and odds using OpenClaw-first routing.

    Returns structured result; never raises.
    """
    routing = resolve_enrichment_routing(
        openclaw_url_override=openclaw_url,
        odds_url_override=odds_url,
        skip_openclaw=skip_openclaw,
        skip_odds=skip_odds,
        mode_override=mode_override,
    )
    token = _build_query_token(
        facts,
        home_override=home_override,
        away_override=away_override,
        date_override=date_override,
        competition_override=competition_override,
        match_url_override=match_url_override,
    )

    sources: Dict[str, str] = {}
    warnings: List[str] = []
    ctx: Optional[OpenClawMatchContext] = None
    odds: Optional[MatchOddsContext] = None

    if not routing.configured:
        sources = {"openclaw": SOURCE_SKIPPED_NOT_CONFIGURED, "odds": SOURCE_SKIPPED_NOT_CONFIGURED}
        from football_agent.services.openclaw_news_enrichment import brave_news_enabled

        if not brave_news_enabled():
            warnings.append("enrichment_not_configured")
            logger.debug("Enrichment skipped — no OpenClaw/odds URL and Brave disabled")
        else:
            logger.debug("OpenClaw/odds not configured — Brave-only enrichment path")
        return _complete_enrichment_result(
            facts=facts,
            ctx=None,
            odds=None,
            sources=sources,
            warnings=warnings,
            routing=routing,
            openclaw_url=openclaw_url,
        )

    if skip_openclaw and skip_odds:
        sources = {"openclaw": SOURCE_SKIPPED, "odds": SOURCE_SKIPPED}
        return _complete_enrichment_result(
            facts=facts,
            ctx=None,
            odds=None,
            sources=sources,
            warnings=warnings,
            routing=routing,
            openclaw_url=openclaw_url,
        )

    if routing.enrichment_mode == ENRICHMENT_MODE_UNIFIED and routing.openclaw_configured and not skip_openclaw:
        ctx, odds, sources, uni_warnings = _fetch_unified(routing, token, openclaw_api_key)
        warnings.extend(uni_warnings)
        unified_transport_failed = any(
            w.startswith("enrichment_unified_fetch_failed:") for w in uni_warnings
        )
        if not unified_transport_failed:
            return _complete_enrichment_result(
                facts=facts,
                ctx=ctx,
                odds=odds,
                sources=sources,
                warnings=warnings,
                routing=routing,
                openclaw_url=openclaw_url,
            )
        logger.info("Unified enrichment transport failed — falling back to split endpoints")
        warnings.append("enrichment_unified_fallback_split")

    if not skip_openclaw:
        ctx, oc_status, oc_warnings = _fetch_context_split(routing, token, openclaw_api_key)
        sources["openclaw"] = oc_status
        warnings.extend(oc_warnings)
    else:
        sources["openclaw"] = SOURCE_SKIPPED

    if not skip_odds:
        odds, odds_status, odds_warnings = _fetch_odds_split(routing, token, odds_api_key)
        sources["odds"] = odds_status
        warnings.extend(odds_warnings)
    else:
        sources["odds"] = SOURCE_SKIPPED

    return _complete_enrichment_result(
        facts=facts,
        ctx=ctx,
        odds=odds,
        sources=sources,
        warnings=warnings,
        routing=routing,
        openclaw_url=openclaw_url,
    )


def _complete_enrichment_result(
    *,
    facts: FlashscoreMatchFacts,
    ctx: Optional[OpenClawMatchContext],
    odds: Optional[MatchOddsContext],
    sources: Dict[str, str],
    warnings: List[str],
    routing: EnrichmentRouting,
    openclaw_url: Optional[str],
) -> EnrichmentFetchResult:
    _annotate_partial_enrichment(ctx, odds, sources, warnings, routing)
    news = _fetch_brave_news_if_enabled(facts, ctx, warnings, sources)
    backend = _backend_label(routing, openclaw_url_override=openclaw_url)
    if backend == "none" and sources.get("brave_news") in (SOURCE_OK, SOURCE_PARTIAL):
        backend = "brave"
    sources["enrichment_backend"] = backend
    return EnrichmentFetchResult(
        context=ctx,
        odds=odds,
        news=news,
        sources=sources,
        warnings=warnings,
        routing=routing,
    )


def _fetch_brave_news_if_enabled(
    facts: FlashscoreMatchFacts,
    openclaw_ctx: Optional[OpenClawMatchContext],
    warnings: List[str],
    sources: Dict[str, str],
) -> Optional[MatchNewsContext]:
    from football_agent.services.openclaw_news_enrichment import brave_news_enabled, enrich_match_news_from_brave

    if not brave_news_enabled():
        sources["brave_news"] = SOURCE_SKIPPED_NOT_CONFIGURED
        return None
    try:
        news = enrich_match_news_from_brave(facts, openclaw_context=openclaw_ctx)
        if news is None:
            sources["brave_news"] = SOURCE_SKIPPED_NOT_CONFIGURED
            return None
        if news.source_count > 0 and news.confidence >= 0.4:
            sources["brave_news"] = SOURCE_OK
        elif news.source_count > 0:
            sources["brave_news"] = SOURCE_PARTIAL
        else:
            sources["brave_news"] = SOURCE_FAILED
            warnings.append("brave_news_empty")
        warnings.extend(news.warnings or [])
        return news
    except Exception as exc:
        warnings.append(f"brave_news_fetch_failed:{exc}")
        sources["brave_news"] = SOURCE_FAILED
        if not config.OPENCLAW_FAIL_SOFT:
            raise
        return None


def _backend_label(routing: EnrichmentRouting, *, openclaw_url_override: Optional[str] = None) -> str:
    if enrichment_uses_bridge(base_url=openclaw_url_override):
        return "openclaw_bridge"
    if routing.openclaw_configured and routing.odds_separate_service:
        return "openclaw+odds_separate"
    if routing.openclaw_configured:
        return "openclaw"
    if routing.odds_separate_service:
        return "odds_separate"
    return "none"


def _annotate_partial_enrichment(
    ctx: Optional[OpenClawMatchContext],
    odds: Optional[MatchOddsContext],
    sources: Dict[str, str],
    warnings: List[str],
    routing: EnrichmentRouting,
) -> None:
    oc = sources.get("openclaw")
    od = sources.get("odds")
    if oc == SOURCE_OK and od == SOURCE_FAILED:
        warnings.append("enrichment_partial:context_without_odds")
        if routing.odds_source == ODDS_SOURCE_OPENCLAW:
            warnings.append("openclaw_odds_missing_in_response")
    elif oc == SOURCE_FAILED and od == SOURCE_OK:
        warnings.append("enrichment_partial:odds_without_context")
    elif oc == SOURCE_PARTIAL:
        warnings.append("enrichment_partial:context_blocks_incomplete")
