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
    is_direct_gateway_url,
    resolve_enrichment_routing,
    resolve_enrichment_routing_with_fallback,
    resolve_legacy_openclaw_gateway_url,
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
    *,
    facts: Optional[FlashscoreMatchFacts] = None,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
    transport: Optional[str] = None,
) -> Tuple[Optional[OpenClawMatchContext], str, List[str]]:
    warnings: List[str] = []
    if not routing.context_base_url:
        return None, SOURCE_SKIPPED_NOT_CONFIGURED, warnings

    base = routing.context_base_url
    use_direct = transport == "direct_gateway" or is_direct_gateway_url(base)
    if use_direct and facts is not None:
        from football_agent.services.direct_gateway_enrichment import (
            fetch_context_via_direct_gateway,
            fetch_context_via_inprocess_bridge,
        )

        bridge_mode = (config.OPENCLAW_BRIDGE_MODE or "prototype").strip().lower()
        if bridge_mode == "prototype":
            ctx, status, ib_warnings = fetch_context_via_inprocess_bridge(
                facts,
                gateway_url=base,
                api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
                home_override=home_override,
                away_override=away_override,
                date_override=date_override,
                competition_override=competition_override,
                match_url_override=match_url_override,
            )
            ib_warnings.append("enrichment_transport_override:inprocess_bridge")
            return ctx, status, ib_warnings

        ctx, status, dg_warnings = fetch_context_via_direct_gateway(
            base,
            facts,
            api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
            home_override=home_override,
            away_override=away_override,
            date_override=date_override,
            competition_override=competition_override,
            match_url_override=match_url_override,
        )
        if status != SOURCE_FAILED:
            return ctx, status, dg_warnings
        if any(
            token in w
            for w in dg_warnings
            for token in ("direct_gateway_context_failed", "backend_endpoint_not_found")
        ):
            ctx2, status2, ib_warnings = fetch_context_via_inprocess_bridge(
                facts,
                gateway_url=base,
                api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
                home_override=home_override,
                away_override=away_override,
                date_override=date_override,
                competition_override=competition_override,
                match_url_override=match_url_override,
            )
            combined = dg_warnings + ib_warnings + ["direct_gateway_chat_failed_inprocess_bridge_fallback"]
            if status2 != SOURCE_FAILED:
                combined.append("enrichment_transport_override:inprocess_bridge")
                return ctx2, status2, combined
        return ctx, status, dg_warnings

    try:
        adapter = HttpOpenClawContextAdapter(
            base,
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
        if facts is not None and _should_fallback_to_direct_gateway(str(exc)):
            from football_agent.services.direct_gateway_enrichment import fetch_context_via_direct_gateway

            gateway = resolve_legacy_openclaw_gateway_url()
            if gateway:
                warnings.append("openclaw_context_bridge_fallback_direct_gateway")
                ctx, status, dg_warnings = fetch_context_via_direct_gateway(
                    gateway,
                    facts,
                    api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
                    home_override=home_override,
                    away_override=away_override,
                    date_override=date_override,
                    competition_override=competition_override,
                    match_url_override=match_url_override,
                )
                warnings.extend(dg_warnings)
                return ctx, status, warnings
        return None, SOURCE_FAILED, warnings


def _should_fallback_to_direct_gateway(error_message: str) -> bool:
    low = (error_message or "").lower()
    return any(
        token in low
        for token in (
            "html",
            "invalid json",
            "connection",
            "refused",
            "unavailable",
            "timeout",
            "404",
            "502",
            "503",
        )
    )


def _fetch_odds_split(
    routing: EnrichmentRouting,
    token: str,
    api_key: Optional[str],
    *,
    facts: Optional[FlashscoreMatchFacts] = None,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
    transport: Optional[str] = None,
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

    base = routing.odds_base_url
    use_direct = transport == "direct_gateway" or is_direct_gateway_url(base)
    if use_direct and facts is not None:
        from football_agent.services.direct_gateway_enrichment import fetch_odds_via_direct_gateway

        ctx, status, dg_warnings = fetch_odds_via_direct_gateway(
            base,
            facts,
            api_key=api_key or config.OPENCLAW_CONTEXT_API_KEY,
            home_override=home_override,
            away_override=away_override,
            date_override=date_override,
            competition_override=competition_override,
            match_url_override=match_url_override,
        )
        warnings.extend(dg_warnings)
        return ctx, status, warnings

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
    resolution = resolve_enrichment_routing_with_fallback(
        openclaw_url_override=openclaw_url,
        odds_url_override=odds_url,
        skip_openclaw=skip_openclaw,
        skip_odds=skip_odds,
        mode_override=mode_override,
    )
    routing = resolution.routing
    transport = resolution.enrichment_backend
    token = _build_query_token(
        facts,
        home_override=home_override,
        away_override=away_override,
        date_override=date_override,
        competition_override=competition_override,
        match_url_override=match_url_override,
    )

    sources: Dict[str, str] = {}
    warnings: List[str] = list(resolution.warnings)
    ctx: Optional[OpenClawMatchContext] = None
    odds: Optional[MatchOddsContext] = None

    if transport == "unavailable" and routing.openclaw_configured and not skip_openclaw:
        warnings.append("openclaw_enrichment_backend_unavailable")
        sources["openclaw"] = SOURCE_FAILED
        sources["odds"] = SOURCE_SKIPPED if skip_odds else SOURCE_FAILED
        backend = _backend_label(routing, openclaw_url_override=openclaw_url, transport=transport)
        sources["enrichment_backend"] = backend
        sources["enrichment_base_url_used"] = resolution.base_url_used or ""
        sources["enrichment_transport"] = transport
        return _complete_enrichment_result(
            facts=facts,
            ctx=None,
            odds=None,
            sources=sources,
            warnings=warnings,
            routing=routing,
            openclaw_url=openclaw_url,
            transport=transport,
            base_url_used=resolution.base_url_used,
        )

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
            transport=transport,
            base_url_used=resolution.base_url_used,
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
            transport=transport,
            base_url_used=resolution.base_url_used,
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
                transport=transport,
                base_url_used=resolution.base_url_used,
            )
        logger.info("Unified enrichment transport failed — falling back to split endpoints")
        warnings.append("enrichment_unified_fallback_split")

    if not skip_openclaw:
        ctx, oc_status, oc_warnings = _fetch_context_split(
            routing,
            token,
            openclaw_api_key,
            facts=facts,
            home_override=home_override,
            away_override=away_override,
            date_override=date_override,
            competition_override=competition_override,
            match_url_override=match_url_override,
            transport=transport,
        )
        sources["openclaw"] = oc_status
        warnings.extend(oc_warnings)
    else:
        sources["openclaw"] = SOURCE_SKIPPED

    if not skip_odds:
        odds, odds_status, odds_warnings = _fetch_odds_split(
            routing,
            token,
            odds_api_key,
            facts=facts,
            home_override=home_override,
            away_override=away_override,
            date_override=date_override,
            competition_override=competition_override,
            match_url_override=match_url_override,
            transport=transport,
        )
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
        transport=transport,
        base_url_used=resolution.base_url_used,
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
    transport: Optional[str] = None,
    base_url_used: Optional[str] = None,
) -> EnrichmentFetchResult:
    _annotate_partial_enrichment(ctx, odds, sources, warnings, routing)
    news = _fetch_enrichment_news(facts, ctx, warnings, sources)
    backend = _backend_label(routing, openclaw_url_override=openclaw_url, transport=transport)
    if any(w.startswith("enrichment_transport_override:inprocess_bridge") for w in warnings):
        backend = "inprocess_bridge"
        transport = "inprocess_bridge"
    if backend in ("none", "unavailable") and sources.get("brave_news") in (SOURCE_OK, SOURCE_PARTIAL, "api_error"):
        backend = "brave"
    sources["enrichment_backend"] = backend
    if base_url_used:
        sources["enrichment_base_url_used"] = base_url_used
    if transport:
        sources["enrichment_transport"] = transport
    return EnrichmentFetchResult(
        context=ctx,
        odds=odds,
        news=news,
        sources=sources,
        warnings=warnings,
        routing=routing,
    )


def _fetch_enrichment_news(
    facts: FlashscoreMatchFacts,
    openclaw_ctx: Optional[OpenClawMatchContext],
    warnings: List[str],
    sources: Dict[str, str],
) -> Optional[MatchNewsContext]:
    """Primary OpenClaw path; Brave only when explicitly enabled as fallback."""
    from football_agent.services.openclaw_primary_enrichment import (
        brave_fallback_allowed,
        openclaw_primary_enrichment,
    )

    if openclaw_primary_enrichment() and openclaw_ctx is not None:
        sources["openclaw_news"] = SOURCE_OK if openclaw_ctx.news else SOURCE_PARTIAL
        sources["enrichment_news_source"] = "openclaw"
        if not brave_fallback_allowed():
            sources["brave_news"] = SOURCE_SKIPPED
            warnings.append("brave_skipped:openclaw_primary")
            return None

    if not brave_fallback_allowed():
        sources["brave_news"] = SOURCE_SKIPPED_NOT_CONFIGURED
        sources["enrichment_news_source"] = "openclaw" if openclaw_ctx else "none"
        return None

    return _fetch_brave_news_if_enabled(facts, openclaw_ctx, warnings, sources)


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
    sources["enrichment_news_source"] = "brave_fallback"
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
        if any("brave_quota_exceeded" in w for w in warnings):
            sources["brave_news"] = "api_error"
        return news
    except Exception as exc:
        from football_agent.services.brave_search_client import is_brave_quota_error

        if is_brave_quota_error(str(exc)):
            warnings.append("brave_quota_exceeded")
            sources["brave_news"] = "api_error"
            return None
        warnings.append(f"brave_news_fetch_failed:{exc}")
        sources["brave_news"] = SOURCE_FAILED
        if not config.OPENCLAW_FAIL_SOFT:
            raise
        return None


def _backend_label(
    routing: EnrichmentRouting,
    *,
    openclaw_url_override: Optional[str] = None,
    transport: Optional[str] = None,
) -> str:
    if transport == "direct_gateway":
        return "direct_gateway"
    if transport == "inprocess_bridge":
        return "inprocess_bridge"
    if transport == "bridge":
        return "bridge"
    if transport == "unavailable":
        return "unavailable"
    if enrichment_uses_bridge(base_url=openclaw_url_override):
        return "bridge"
    if is_direct_gateway_url(routing.openclaw_base_url):
        return "direct_gateway"
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
