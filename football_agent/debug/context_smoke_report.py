"""
Rich JSON + human summary for arbitrary Flashscore URL live-debug smoke.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from football_agent.debug.enrichment_diagnostics import diagnose_context_blocks
from football_agent.services.enrichment_contract import SOURCE_FAILED, SOURCE_OK, SOURCE_PARTIAL


def _openclaw_status(sources: Dict[str, str], *, openclaw_requested: bool) -> str:
    if not openclaw_requested:
        return "skipped"
    oc = sources.get("openclaw")
    transport = sources.get("enrichment_transport")
    if transport == "unavailable" or oc == SOURCE_FAILED:
        return "failed"
    if oc in (SOURCE_OK, SOURCE_PARTIAL):
        return "partial" if oc == SOURCE_PARTIAL else "ok"
    if oc == "api_error":
        return "partial"
    return "failed"


def _block_status(
    summary: Dict[str, Any],
    block_key: str,
    *,
    source_keys: tuple[str, ...] = (),
) -> str:
    sources = summary.get("sources") or {}
    for key in source_keys:
        val = sources.get(key)
        if val in (SOURCE_OK, SOURCE_PARTIAL):
            return "ok" if val == SOURCE_OK else "partial"
        if val == "api_error":
            return "quota_degraded"
        if val == SOURCE_FAILED:
            return "failed"
    rep = summary.get("report") or {}
    missing = rep.get("merge_missing_blocks") or []
    if block_key in missing:
        return "missing"
    return "unknown"


def _top_underfed_blocks(summary: Dict[str, Any], *, limit: int = 8) -> List[str]:
    rep = summary.get("report") or {}
    missing = list(rep.get("merge_missing_blocks") or [])
    comp = summary.get("completeness") or {}
    flash_missing = comp.get("flashscore_missing") or []
    combined = missing + [m for m in flash_missing if m not in missing]
    return combined[:limit]


def _brave_warnings(source_warnings: List[str]) -> List[str]:
    return [w for w in source_warnings if "brave_quota" in w or "brave_query_failed" in w]


def _openclaw_enriched(summary: Dict[str, Any]) -> bool:
    sources = summary.get("sources") or {}
    oc = sources.get("openclaw")
    if oc not in (SOURCE_OK, SOURCE_PARTIAL):
        return False
    rep = summary.get("report") or {}
    link = rep.get("openclaw_link_strategy")
    return link in ("linked", "partial_link", "provided_with_link")


def _scorer_impact_summary(pipeline_summary: Dict[str, Any]) -> Dict[str, Any]:
    scoring = pipeline_summary.get("scoring") or {}
    factor = scoring.get("factor_inspection") or {}
    home = factor.get("home") or {}
    away = factor.get("away") or {}
    conf = factor.get("snapshot_confidence") or pipeline_summary.get("completeness") or {}
    sources = pipeline_summary.get("sources") or {}

    def _side_factors(side: Dict[str, Any]) -> Dict[str, Any]:
        fs = side.get("factor_scores") or {}
        wc = side.get("weighted_contributions") or {}
        return {
            "squad_availability": fs.get("squad_availability"),
            "coach_factor": fs.get("coach_factor"),
            "motivation": fs.get("motivation"),
            "schedule_context": fs.get("schedule_context"),
            "total_score": fs.get("total_score"),
            "weighted_share_pct": wc.get("share_pct"),
            "summary_flags": side.get("summary_flags"),
        }

    return {
        "enrichment_news_source": sources.get("enrichment_news_source", "unknown"),
        "brave_used": sources.get("brave_news") not in (None, "skipped", "skipped_not_configured"),
        "openclaw_used": sources.get("openclaw") in ("ok", "partial"),
        "home_factors": _side_factors(home),
        "away_factors": _side_factors(away),
        "confidence": {
            "squads": conf.get("squads_confidence"),
            "coaches": conf.get("coaches_confidence"),
            "news": conf.get("news_confidence"),
            "schedule": conf.get("schedule_confidence"),
            "overall": conf.get("overall_confidence_score"),
        },
        "express_safety": (scoring.get("express_safety") or {}).get("safety_class"),
    }


def build_human_summary(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    match = report.get("match") or {}
    lines.append(
        f"Match: {match.get('home')} vs {match.get('away')} "
        f"({match.get('competition') or '—'})"
    )
    lines.append(f"URL: {match.get('match_url') or '—'}")
    lines.append(f"Scenario: {report.get('scenario')}")
    lines.append(f"Pipeline success: {report.get('success')}")

    oc = report.get("openclaw") or {}
    lines.append(
        f"OpenClaw: status={oc.get('status')} backend={oc.get('enrichment_backend')} "
        f"url={oc.get('base_url_used') or '—'}"
    )
    lines.append(
        f"Blocks: coaches={oc.get('coaches_status')} squads={oc.get('squads_status')} "
        f"news={oc.get('news_status')}"
    )
    lines.append(f"OpenClaw enriched merge: {oc.get('enriched_useful')}")

    sources = report.get("sources") or {}
    lines.append(f"Sources: {sources}")

    brave = report.get("brave") or {}
    if brave.get("quota_limited"):
        lines.append("Brave: quota exceeded — news degradation only (not OpenClaw failure)")
    elif brave.get("warnings"):
        lines.append(f"Brave warnings: {', '.join(brave['warnings'][:3])}")

    underfed = report.get("top_underfed_blocks") or []
    if underfed:
        lines.append(f"Top underfed: {', '.join(underfed)}")

    impact = report.get("scorer_impact") or {}
    if impact:
        lines.append(
            f"Scorer impact: news_source={impact.get('enrichment_news_source')} "
            f"brave_used={impact.get('brave_used')} openclaw_used={impact.get('openclaw_used')}"
        )
        hf = impact.get("home_factors") or {}
        lines.append(
            f"  home: squad={hf.get('squad_availability')} coach={hf.get('coach_factor')} "
            f"motivation={hf.get('motivation')} schedule={hf.get('schedule_context')}"
        )

    warns = report.get("source_warnings") or []
    non_brave = [w for w in warns if "brave" not in w.lower()][:5]
    if non_brave:
        lines.append(f"Other warnings: {'; '.join(non_brave)}")

    return "\n".join(lines)


def build_context_smoke_report(
    *,
    match_url: str,
    scenario: str,
    services: Dict[str, Any],
    pipeline_summary: Dict[str, Any],
) -> Dict[str, Any]:
    sources = dict(pipeline_summary.get("sources") or {})
    source_warnings = list(pipeline_summary.get("source_warnings") or [])
    meta = pipeline_summary.get("snapshot_meta") or {}
    rep = pipeline_summary.get("report") or {}
    pipeline = pipeline_summary.get("pipeline") or {}

    openclaw_requested = bool(pipeline.get("openclaw_requested", True))
    oc_status = _openclaw_status(sources, openclaw_requested=openclaw_requested)
    ctx_diag = diagnose_context_blocks(None)
    if pipeline_summary.get("success"):
        ctx_diag = diagnose_context_blocks_from_summary(pipeline_summary)

    coaches_status = _block_status(pipeline_summary, "coach_context", source_keys=("openclaw", "brave_news"))
    squads_status = _block_status(pipeline_summary, "squad_context", source_keys=("openclaw", "brave_news"))
    news_status = _block_status(
        pipeline_summary,
        "news_context",
        source_keys=("brave_news", "openclaw"),
    )
    if sources.get("brave_news") == "api_error":
        news_status = "quota_degraded"

    brave_warns = _brave_warnings(source_warnings)
    quota_limited = any("brave_quota_exceeded" in w for w in source_warnings)

    report: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "match_url": match_url,
        "success": pipeline_summary.get("success"),
        "services": services,
        "match": {
            "match_id": meta.get("match_id") or pipeline_summary.get("match_key"),
            "home": (meta.get("home_team") or {}).get("name"),
            "away": (meta.get("away_team") or {}).get("name"),
            "competition": meta.get("competition_name"),
            "kickoff_utc": meta.get("kickoff_utc"),
            "match_url": match_url,
        },
        "sources": sources,
        "source_warnings": source_warnings,
        "completeness": pipeline_summary.get("completeness"),
        "openclaw": {
            "requested": openclaw_requested,
            "status": oc_status,
            "enrichment_backend": sources.get("enrichment_backend") or pipeline.get("enrichment_backend"),
            "enrichment_transport": sources.get("enrichment_transport"),
            "base_url_used": sources.get("enrichment_base_url_used"),
            "coaches_status": coaches_status,
            "squads_status": squads_status,
            "news_status": news_status,
            "enriched_useful": _openclaw_enriched(pipeline_summary),
            "context_blocks": ctx_diag.get("blocks_present") or [],
            "context_missing": ctx_diag.get("missing_blocks") or [],
        },
        "brave": {
            "quota_limited": quota_limited,
            "warnings": brave_warns[:10],
        },
        "top_underfed_blocks": _top_underfed_blocks(pipeline_summary),
        "scorer_impact": _scorer_impact_summary(pipeline_summary),
        "report": {
            "merge_missing_blocks": rep.get("merge_missing_blocks"),
            "openclaw_link_strategy": rep.get("openclaw_link_strategy"),
            "odds_link_strategy": rep.get("odds_link_strategy"),
        },
        "scoring": pipeline_summary.get("scoring"),
    }
    report["human_summary"] = build_human_summary(report)
    return report


def diagnose_context_blocks_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort block diagnosis from merge report when context object unavailable."""
    rep = summary.get("report") or {}
    missing = set(rep.get("merge_missing_blocks") or [])
    all_blocks = [
        "news",
        "squad_context",
        "coach_context",
        "motivation_narrative",
        "fatigue_schedule_context",
    ]
    present = [b for b in all_blocks if b not in missing and "openclaw" not in missing]
    if "openclaw_context" in missing:
        return {
            "present": False,
            "blocks_present": [],
            "missing_blocks": list(all_blocks),
            "contract_ok": False,
        }
    oc_missing = [b for b in all_blocks if f"openclaw_{b}" in missing or b in missing]
    blocks_present = [b for b in all_blocks if b not in oc_missing]
    return {
        "present": bool(blocks_present),
        "blocks_present": blocks_present,
        "missing_blocks": oc_missing,
        "contract_ok": bool(blocks_present),
    }
