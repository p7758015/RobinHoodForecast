"""
Pipeline-level source completeness and provenance reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.raw_enrich import assess_block_signals
from football_agent.odds.models import MatchOddsContext
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.services.enrichment_contract import SOURCE_SKIPPED_NOT_CONFIGURED


@dataclass
class SourceCompletenessReport:
    flashscore_blocks: Dict[str, bool] = field(default_factory=dict)
    flashscore_missing: List[str] = field(default_factory=list)
    openclaw_status: str = "skipped"
    odds_status: str = "skipped"
    odds_markets_filled: int = 0
    odds_markets_missing: int = 0
    openclaw_link: Optional[str] = None
    odds_link: Optional[str] = None
    enrichment_mode: str = "not_configured"
    odds_source: str = "none"
    enrichment_backend: Optional[str] = None
    competition_context: Optional[str] = None
    competition_guardrail_applied: bool = False
    warnings: List[str] = field(default_factory=list)

    def coverage_score(self) -> float:
        """0..1 rough completeness for debug (not used in scoring)."""
        fs_total = len(self.flashscore_blocks) or 1
        fs_ok = sum(1 for v in self.flashscore_blocks.values() if v)
        fs_part = fs_ok / fs_total
        oc_part = 1.0 if self.openclaw_status in ("ok", "partial") else 0.0
        odds_part = 1.0 if self.odds_status in ("ok", "partial") else 0.0
        if self.odds_status == SOURCE_SKIPPED_NOT_CONFIGURED:
            odds_part = 0.0
        return round((fs_part * 0.5) + (oc_part * 0.25) + (odds_part * 0.25), 2)

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "flashscore_blocks": dict(self.flashscore_blocks),
            "flashscore_missing": list(self.flashscore_missing),
            "openclaw_status": self.openclaw_status,
            "odds_status": self.odds_status,
            "odds_markets_filled": self.odds_markets_filled,
            "odds_markets_missing": self.odds_markets_missing,
            "openclaw_link": self.openclaw_link,
            "odds_link": self.odds_link,
            "enrichment_mode": self.enrichment_mode,
            "odds_source": self.odds_source,
            "enrichment_backend": self.enrichment_backend,
            "competition_context": self.competition_context,
            "competition_guardrail_applied": self.competition_guardrail_applied,
            "coverage_score": self.coverage_score(),
            "warnings": list(self.warnings),
        }


def _safe_link(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def build_completeness_report(
    *,
    facts: FlashscoreMatchFacts,
    sources: Dict[str, str],
    warnings: List[str],
    openclaw_ctx: Optional[OpenClawMatchContext],
    odds_ctx: Optional[MatchOddsContext],
    openclaw_link: Optional[str] = None,
    odds_link: Optional[str] = None,
    enrichment_mode: Optional[str] = None,
    odds_source: Optional[str] = None,
    enrichment_backend: Optional[str] = None,
    competition_context: Optional[str] = None,
    competition_guardrail_applied: bool = False,
) -> SourceCompletenessReport:
    raw_signals = assess_block_signals(_facts_to_raw_dict(facts))
    missing = [k for k, ok in raw_signals.items() if not ok]

    report = SourceCompletenessReport(
        flashscore_blocks=raw_signals,
        flashscore_missing=missing,
        openclaw_status=sources.get("openclaw", "skipped"),
        odds_status=sources.get("odds", "skipped"),
        openclaw_link=_safe_link(openclaw_link),
        odds_link=_safe_link(odds_link),
        enrichment_mode=enrichment_mode or "not_configured",
        odds_source=odds_source or "none",
        enrichment_backend=enrichment_backend,
        competition_context=competition_context,
        competition_guardrail_applied=competition_guardrail_applied,
        warnings=list(warnings),
    )

    if openclaw_ctx and openclaw_ctx.provenance:
        report.warnings.extend(openclaw_ctx.provenance.extraction_warnings[:3])
    if odds_ctx and odds_ctx.provenance:
        missing_m = odds_ctx.provenance.missing_markets or []
        report.odds_markets_missing = len(missing_m)
        report.odds_markets_filled = max(0, 8 - len(missing_m))

    if facts.provenance.parsing_warnings:
        report.warnings.extend(facts.provenance.parsing_warnings[:5])

    return report


def _facts_to_raw_dict(facts: FlashscoreMatchFacts) -> Dict[str, Any]:
    return {
        "standings": facts.standings.model_dump() if facts.standings else {},
        "season_context": facts.season_context_inputs.model_dump() if facts.season_context_inputs else {},
        "form": facts.form.model_dump() if facts.form else {},
        "h2h": facts.h2h.model_dump() if facts.h2h else {},
        "squad_raw": facts.squad_raw.model_dump() if facts.squad_raw else {},
        "schedule_raw": facts.schedule_raw.model_dump() if facts.schedule_raw else {},
        "stats_raw": facts.stats_raw.model_dump() if facts.stats_raw else {},
    }


def format_telegram_completeness_hint(report: SourceCompletenessReport) -> Optional[str]:
    """One short line for Telegram when data is partial."""
    parts: List[str] = []
    if report.flashscore_missing:
        short = report.flashscore_missing[:3]
        parts.append("FS: " + ", ".join(short))
    if report.openclaw_status == SOURCE_SKIPPED_NOT_CONFIGURED:
        parts.append("OpenClaw не подключён")
    elif report.openclaw_status not in ("ok", "partial"):
        parts.append(f"OC: {report.openclaw_status}")
    if report.odds_status == SOURCE_SKIPPED_NOT_CONFIGURED:
        if report.odds_source == "openclaw" and report.openclaw_status in ("ok", "partial"):
            parts.append("линия: не в ответе OpenClaw")
        elif report.enrichment_mode != "not_configured":
            pass
        else:
            parts.append("линия: не настроена")
    elif report.odds_status not in ("ok", "partial"):
        parts.append(f"линия: {report.odds_status}")
    if not parts:
        return None
    return "Полнота данных: " + "; ".join(parts)
