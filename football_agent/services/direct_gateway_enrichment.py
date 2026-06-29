"""
Direct OpenClaw gateway enrichment via OpenAI-compatible chat API.

Used when the local bridge (8787) is down but the gateway tunnel (18789) is healthy.
Debug/live enrichment only — same contract shapes as bridge HTTP responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from football_agent import config
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.models import MatchOddsContext
from football_agent.openclaw_bridge.backend_client import OpenClawChatBackend
from football_agent.openclaw_bridge.enricher import BridgeEnricher
from football_agent.openclaw_bridge.models import BridgeMatchInput, BridgeMode
from football_agent.openclaw_bridge.normalizer import normalize_context_blocks, normalize_odds_markets
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.odds.service import OddsIngestionService
from football_agent.services.enrichment_contract import SOURCE_FAILED, SOURCE_OK, SOURCE_PARTIAL

DIRECT_GATEWAY_BACKEND_NAME = "direct_gateway"
INPROCESS_BRIDGE_BACKEND_NAME = "inprocess_bridge"


def _bridge_mode_from_config() -> BridgeMode:
    mode = (config.OPENCLAW_BRIDGE_MODE or "prototype").strip().lower()
    if mode == BridgeMode.LIVE_ASSISTED.value:
        return BridgeMode.LIVE_ASSISTED
    return BridgeMode.PROTOTYPE


def _bridge_enricher(gateway_url: Optional[str], api_key: Optional[str]) -> BridgeEnricher:
    return BridgeEnricher(
        mode=_bridge_mode_from_config(),
        openclaw_gateway_url=gateway_url,
        api_key=api_key or config.OPENCLAW_BRIDGE_API_KEY or config.OPENCLAW_API_KEY,
        model=config.OPENCLAW_BRIDGE_MODEL,
        chat_path=config.OPENCLAW_BRIDGE_CHAT_PATH,
        live_timeout_s=config.OPENCLAW_BRIDGE_LIVE_TIMEOUT_S,
    )


def bridge_input_from_facts(
    facts: FlashscoreMatchFacts,
    *,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
) -> BridgeMatchInput:
    kickoff = facts.meta.kickoff_utc.isoformat() if facts.meta.kickoff_utc else None
    date_str = date_override
    if not date_str and facts.meta.kickoff_utc:
        date_str = facts.meta.kickoff_utc.date().isoformat()

    home_form = ""
    away_form = ""
    if facts.form:
        if facts.form.home and facts.form.home.last_n_results:
            home_form = " ".join(facts.form.home.last_n_results[:5])
        if facts.form.away and facts.form.away.last_n_results:
            away_form = " ".join(facts.form.away.last_n_results[:5])

    standings = ""
    if facts.standings:
        standings = (
            f"home_pos={facts.standings.home_position} "
            f"away_pos={facts.standings.away_position}"
        )

    coach_home = None
    coach_away = None
    if facts.squad_raw:
        coach_home = facts.squad_raw.coach_name_home
        coach_away = facts.squad_raw.coach_name_away

    return BridgeMatchInput(
        home_team=home_override or facts.meta.home_team_name,
        away_team=away_override or facts.meta.away_team_name,
        competition_name=competition_override or facts.meta.competition_name,
        kickoff_utc=kickoff,
        date=date_str,
        country=facts.meta.competition_country,
        match_url=match_url_override or facts.meta.source_url,
        match_id=facts.meta.match_id,
        home_form_summary=home_form or None,
        away_form_summary=away_form or None,
        standings_summary=standings or None,
        coach_name_home=coach_home,
        coach_name_away=coach_away,
    )


def _chat_backend(gateway_url: str, api_key: Optional[str]) -> OpenClawChatBackend:
    return OpenClawChatBackend(
        gateway_url,
        api_key=api_key or config.OPENCLAW_BRIDGE_API_KEY or config.OPENCLAW_API_KEY,
        model=config.OPENCLAW_BRIDGE_MODEL,
        chat_path=config.OPENCLAW_BRIDGE_CHAT_PATH,
        timeout_s=config.OPENCLAW_BRIDGE_LIVE_TIMEOUT_S,
    )


def _context_raw_from_blocks(
    facts: FlashscoreMatchFacts,
    blocks: Dict[str, Any],
    *,
    extraction_warnings: List[str],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        **blocks,
        "match_id": facts.meta.match_id,
        "query_home_team": facts.meta.home_team_name,
        "query_away_team": facts.meta.away_team_name,
        "query_competition_name": facts.meta.competition_name,
        "query_kickoff_utc": facts.meta.kickoff_utc.isoformat() if facts.meta.kickoff_utc else None,
        "collected_at_utc": now,
        "backend_name": DIRECT_GATEWAY_BACKEND_NAME,
        "extraction_warnings": list(extraction_warnings),
    }


def fetch_context_via_direct_gateway(
    gateway_url: str,
    facts: FlashscoreMatchFacts,
    *,
    api_key: Optional[str] = None,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
) -> Tuple[Optional[OpenClawMatchContext], str, List[str]]:
    inp = bridge_input_from_facts(
        facts,
        home_override=home_override,
        away_override=away_override,
        date_override=date_override,
        competition_override=competition_override,
        match_url_override=match_url_override,
    )
    if not inp.home_team or not inp.away_team:
        return None, SOURCE_FAILED, ["direct_gateway_missing_team_names"]

    backend = _chat_backend(gateway_url, api_key)
    result = backend.fetch_context_blocks(inp)
    warnings = list(result.warnings or [])
    if not result.data:
        warnings.append("direct_gateway_context_failed")
        return None, SOURCE_FAILED, warnings

    normalized = normalize_context_blocks(result.data)
    if not normalized:
        warnings.append("direct_gateway_context_empty_blocks")
        return None, SOURCE_FAILED, warnings

    raw = _context_raw_from_blocks(facts, normalized, extraction_warnings=warnings)

    class _RawAdapter:
        def fetch_context_raw(self, _token: str) -> dict:
            return raw

    ctx = OpenClawContextIngestionService(_RawAdapter()).get_context_for_fixture("inline")
    if ctx is None:
        warnings.append("direct_gateway_context_map_failed")
        return None, SOURCE_FAILED, warnings

    status = SOURCE_OK
    if ctx.provenance.missing_blocks:
        status = SOURCE_PARTIAL
    return ctx, status, warnings


def fetch_context_via_inprocess_bridge(
    facts: FlashscoreMatchFacts,
    *,
    gateway_url: Optional[str] = None,
    api_key: Optional[str] = None,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
) -> Tuple[Optional[OpenClawMatchContext], str, List[str]]:
    """
    Run ``BridgeEnricher`` in-process when bridge HTTP server is down.

    In ``prototype`` mode returns contract-shaped stub blocks without HTTP 8787.
    In ``live_assisted`` mode tries gateway chat then prototype fallback inside enricher.
    """
    inp = bridge_input_from_facts(
        facts,
        home_override=home_override,
        away_override=away_override,
        date_override=date_override,
        competition_override=competition_override,
        match_url_override=match_url_override,
    )
    enricher = _bridge_enricher(gateway_url, api_key)
    envelope = enricher.enrich_context(inp)
    warnings = list(envelope.warnings or [])
    raw = envelope.to_context_response()
    raw["backend_name"] = INPROCESS_BRIDGE_BACKEND_NAME
    raw.setdefault("query_home_team", facts.meta.home_team_name)
    raw.setdefault("query_away_team", facts.meta.away_team_name)
    raw.setdefault("query_competition_name", facts.meta.competition_name)

    class _RawAdapter:
        def fetch_context_raw(self, _token: str) -> dict:
            return raw

    ctx = OpenClawContextIngestionService(_RawAdapter()).get_context_for_fixture("inline")
    if ctx is None:
        warnings.append("inprocess_bridge_context_map_failed")
        return None, SOURCE_FAILED, warnings
    status = SOURCE_OK if envelope.completeness >= 0.6 else SOURCE_PARTIAL
    if envelope.completeness <= 0:
        status = SOURCE_FAILED
    return ctx, status, warnings


def fetch_odds_via_direct_gateway(
    gateway_url: str,
    facts: FlashscoreMatchFacts,
    *,
    api_key: Optional[str] = None,
    home_override: Optional[str] = None,
    away_override: Optional[str] = None,
    date_override: Optional[str] = None,
    competition_override: Optional[str] = None,
    match_url_override: Optional[str] = None,
) -> Tuple[Optional[MatchOddsContext], str, List[str]]:
    inp = bridge_input_from_facts(
        facts,
        home_override=home_override,
        away_override=away_override,
        date_override=date_override,
        competition_override=competition_override,
        match_url_override=match_url_override,
    )
    if not inp.home_team or not inp.away_team:
        return None, SOURCE_FAILED, ["direct_gateway_missing_team_names"]

    backend = _chat_backend(gateway_url, api_key)
    result = backend.fetch_odds_markets(inp)
    warnings = list(result.warnings or [])
    if not result.data:
        warnings.append("direct_gateway_odds_failed")
        return None, SOURCE_FAILED, warnings

    markets = normalize_odds_markets(result.data)
    if not markets:
        warnings.append("direct_gateway_odds_empty_markets")
        return None, SOURCE_FAILED, warnings

    raw: Dict[str, Any] = {
        "fixture_id": facts.meta.match_id or "direct-gateway",
        "match_id": facts.meta.match_id,
        "markets": markets,
        "backend_name": DIRECT_GATEWAY_BACKEND_NAME,
        "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "extraction_warnings": warnings,
    }

    class _RawAdapter:
        def fetch_odds_raw(self, _token: str) -> dict:
            return raw

    ctx = OddsIngestionService(_RawAdapter()).get_odds_for_fixture("inline")
    if ctx is None:
        warnings.append("direct_gateway_odds_map_failed")
        return None, SOURCE_FAILED, warnings
    return ctx, SOURCE_OK, warnings
