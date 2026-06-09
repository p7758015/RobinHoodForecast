"""
Contract diagnostics and smoke-report builders for OpenClaw enrichment.

Ops/debug only — not used in Telegram output.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.models import MatchOddsContext
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.services.enrichment_config import EnrichmentRouting
from football_agent.services.enrichment_contract import (
    ENRICHMENT_CONTEXT_PATH,
    ENRICHMENT_MODE_UNIFIED,
    ENRICHMENT_ODDS_PATH,
    ENRICHMENT_UNIFIED_PATH,
    ODDS_SOURCE_OPENCLAW,
    ODDS_SOURCE_SEPARATE,
    SOURCE_FAILED,
    SOURCE_OK,
    SOURCE_PARTIAL,
    SOURCE_SKIPPED,
    SOURCE_SKIPPED_NOT_CONFIGURED,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult
from football_agent.services.http_fetch_result import classify_http_error_message

def redact_secrets(text: str) -> str:
    out = text or ""
    out = re.sub(r"(?i)(api[_-]?key)\s*[:=]\s*\S+", r"\1=***", out)
    out = re.sub(r"(?i)authorization\s*:\s*Bearer\s+\S+", "Authorization: Bearer ***", out)
    out = re.sub(r"(?i)authorization\s*:\s*\S+", "Authorization: ***", out)
    out = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", out)
    return out


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def build_endpoints_view(routing: EnrichmentRouting) -> Dict[str, Optional[str]]:
    base = routing.openclaw_base_url
    if routing.enrichment_mode == ENRICHMENT_MODE_UNIFIED and base:
        return {
            "unified": f"{base}{ENRICHMENT_UNIFIED_PATH}",
            "context": None,
            "odds": None,
        }
    ctx_url = f"{routing.context_base_url}{ENRICHMENT_CONTEXT_PATH}" if routing.context_base_url else None
    odds_url = f"{routing.odds_base_url}{ENRICHMENT_ODDS_PATH}" if routing.odds_base_url else None
    return {
        "unified": f"{base}{ENRICHMENT_UNIFIED_PATH}" if base else None,
        "context": ctx_url,
        "odds": odds_url,
    }


def diagnose_context_blocks(ctx: Optional[OpenClawMatchContext]) -> Dict[str, Any]:
    if ctx is None:
        return {"present": False, "blocks_present": [], "missing_blocks": [], "contract_ok": False}
    prov = ctx.provenance
    return {
        "present": True,
        "blocks_present": list(prov.blocks_present),
        "missing_blocks": list(prov.missing_blocks),
        "extraction_warnings": list(prov.extraction_warnings[:5]),
        "contract_ok": bool(prov.blocks_present),
        "news_items": (
            len(ctx.news.home_news_items)
            + len(ctx.news.away_news_items)
            + len(ctx.news.match_news_items)
            if ctx.news
            else 0
        ),
    }


def diagnose_odds_blocks(odds: Optional[MatchOddsContext]) -> Dict[str, Any]:
    if odds is None:
        return {"present": False, "markets_filled": [], "markets_missing": [], "contract_ok": False}
    markets = odds.markets
    filled: List[str] = []
    for key in (
        "home_win",
        "away_win",
        "double_chance_1x",
        "double_chance_x2",
        "btts_yes",
        "home_team_to_score_yes",
        "away_team_to_score_yes",
        "over_1_5",
        "under_3_5",
    ):
        if getattr(markets, key, None) is not None:
            filled.append(key)
    prov = odds.provenance
    return {
        "present": True,
        "markets_filled": filled,
        "markets_missing": list(prov.missing_markets),
        "extraction_warnings": list(prov.extraction_warnings[:5]),
        "contract_ok": len(filled) > 0,
        "bookmaker_hint": filled[0] if filled else None,
    }


def extract_failure_reasons(warnings: List[str]) -> List[str]:
    reasons: List[str] = []
    for w in warnings:
        low = w.lower()
        for prefix in (
            "openclaw_context_fetch_failed:",
            "odds_fetch_failed:",
            "enrichment_unified_fetch_failed:",
            "enrichment_unified_detail:",
            "openclaw_context_detail:",
            "odds_detail:",
        ):
            if w.startswith(prefix):
                body = w.split(":", 1)[-1].strip()
                reason = classify_http_error_message(body) if prefix.endswith(":") else body
                reasons.append(f"{prefix.rstrip(':')}={reason}")
                break
        if w == "enrichment_not_configured":
            reasons.append("backend_not_configured")
        if w == "enrichment_unified_fallback_split":
            reasons.append("unified_fallback_split")
    return list(dict.fromkeys(reasons))


def classify_payload_completeness(result: EnrichmentFetchResult) -> str:
    oc = result.sources.get("openclaw")
    od = result.sources.get("odds")
    if oc == SOURCE_SKIPPED_NOT_CONFIGURED and od == SOURCE_SKIPPED_NOT_CONFIGURED:
        return "not_configured"
    if oc in (SOURCE_OK, SOURCE_PARTIAL) and od in (SOURCE_OK, SOURCE_PARTIAL):
        return "full"
    if oc in (SOURCE_OK, SOURCE_PARTIAL) or od in (SOURCE_OK, SOURCE_PARTIAL):
        return "partial"
    if oc == SOURCE_FAILED or od == SOURCE_FAILED:
        return "failed"
    return "missing"


def infer_contract_issues(result: EnrichmentFetchResult) -> List[str]:
    issues: List[str] = []
    warnings = result.warnings

    if any(w.startswith("enrichment_unified_bad_payload") for w in warnings):
        issues.append("unified_payload_shape_invalid")
    if any(w.startswith("enrichment_unified_context_not_object") for w in warnings):
        issues.append("unified_context_block_wrong_type")
    if any(w.startswith("enrichment_unified_odds_not_object") for w in warnings):
        issues.append("unified_odds_block_wrong_type")
    if any(w.startswith("enrichment_unified_empty") for w in warnings):
        issues.append("unified_response_empty")
    if any(w.startswith("enrichment_unified_context_empty_blocks") for w in warnings):
        issues.append("unified_context_blocks_empty")
    if any(w.startswith("enrichment_unified_odds_empty_markets") for w in warnings):
        issues.append("unified_odds_markets_empty")
    if "enrichment_partial:context_without_odds" in warnings:
        issues.append("context_ok_odds_missing")
    if "enrichment_partial:odds_without_context" in warnings:
        issues.append("odds_ok_context_missing")
    if "enrichment_unified_fallback_split" in warnings:
        issues.append("split_fallback_used")

    oc = result.sources.get("openclaw")
    od = result.sources.get("odds")
    if oc == SOURCE_FAILED and any("auth" in w for w in extract_failure_reasons(warnings)):
        issues.append("auth_error")
    if oc == SOURCE_FAILED and any("timeout" in w for w in extract_failure_reasons(warnings)):
        issues.append("timeout")
    if od == SOURCE_FAILED and result.context is not None:
        issues.append("odds_endpoint_or_payload_problem")

    if result.context and not diagnose_context_blocks(result.context)["contract_ok"]:
        issues.append("context_contract_incomplete")
    if result.odds and not diagnose_odds_blocks(result.odds)["contract_ok"]:
        issues.append("odds_contract_incomplete")

    return list(dict.fromkeys(issues))


def odds_source_label(routing: Optional[EnrichmentRouting]) -> str:
    if routing is None:
        return "none"
    if routing.odds_source == ODDS_SOURCE_OPENCLAW:
        return "openclaw"
    if routing.odds_source == ODDS_SOURCE_SEPARATE:
        return "legacy_odds_override"
    return "none"


def build_smoke_diagnostic(
    *,
    facts: FlashscoreMatchFacts,
    result: EnrichmentFetchResult,
    mode_requested: str,
    flashscore_status: str = "not_requested",
) -> Dict[str, Any]:
    routing = result.routing
    configured = bool(routing and routing.configured)
    endpoints = build_endpoints_view(routing) if routing else {"unified": None, "context": None, "odds": None}

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "match": {
            "match_id": facts.meta.match_id,
            "home": facts.meta.home_team_name,
            "away": facts.meta.away_team_name,
            "competition": facts.meta.competition_name,
            "kickoff_utc": _iso(facts.meta.kickoff_utc),
            "source_url": facts.meta.source_url,
        },
        "flashscore_status": flashscore_status,
        "enrichment": {
            "configured": configured,
            "mode_requested": mode_requested,
            "mode_resolved": result.enrichment_mode,
            "endpoints": endpoints,
            "odds_source": odds_source_label(routing),
            "enrichment_backend": result.sources.get("enrichment_backend"),
        },
        "status": {
            "context": result.sources.get("openclaw"),
            "odds": result.sources.get("odds"),
            "completeness": classify_payload_completeness(result),
        },
        "contract": {
            "context": diagnose_context_blocks(result.context),
            "odds": diagnose_odds_blocks(result.odds),
            "issues": infer_contract_issues(result),
        },
        "warnings": [redact_secrets(w) for w in result.warnings],
        "failure_reasons": extract_failure_reasons(result.warnings),
        "split_fallback": "enrichment_unified_fallback_split" in result.warnings,
    }


def format_smoke_summary(diag: Dict[str, Any]) -> str:
    lines: List[str] = ["OpenClaw enrichment smoke"]
    match = diag.get("match") or {}
    lines.append(f"Match: {match.get('home')} — {match.get('away')} ({match.get('competition') or '—'})")

    enrich = diag.get("enrichment") or {}
    configured = enrich.get("configured")
    lines.append(f"Backend: {'configured' if configured else 'NOT configured'}")
    lines.append(f"Mode: requested={enrich.get('mode_requested')} resolved={enrich.get('mode_resolved')}")
    lines.append(f"Odds source: {enrich.get('odds_source')}")

    endpoints = enrich.get("endpoints") or {}
    if endpoints.get("unified") and enrich.get("mode_resolved") == ENRICHMENT_MODE_UNIFIED:
        lines.append(f"Endpoint: {endpoints.get('unified')}")
    else:
        if endpoints.get("context"):
            lines.append(f"Context: {endpoints.get('context')}")
        if endpoints.get("odds"):
            lines.append(f"Odds: {endpoints.get('odds')}")

    status = diag.get("status") or {}
    lines.append(f"Context status: {status.get('context')}")
    lines.append(f"Odds status: {status.get('odds')}")
    lines.append(f"Payload completeness: {status.get('completeness')}")

    contract = diag.get("contract") or {}
    ctx_c = contract.get("context") or {}
    odds_c = contract.get("odds") or {}
    if ctx_c.get("present"):
        lines.append(
            "Context blocks: "
            + (", ".join(ctx_c.get("blocks_present") or []) or "none")
        )
    if odds_c.get("present"):
        lines.append(
            "Odds markets: "
            + (", ".join(odds_c.get("markets_filled") or []) or "none")
        )

    issues = contract.get("issues") or []
    if issues:
        lines.append("Contract issues: " + ", ".join(issues[:5]))

    reasons = diag.get("failure_reasons") or []
    if reasons:
        lines.append("Failure reasons: " + ", ".join(reasons[:5]))

    if diag.get("split_fallback"):
        lines.append("Note: unified transport failed — split fallback was used")

    warns = diag.get("warnings") or []
    if warns:
        lines.append("Warnings: " + "; ".join(redact_secrets(w) for w in warns[:3]))

    return "\n".join(lines)
