"""Compact Telegram reply formatting for single-match v2 analysis."""

from __future__ import annotations

from typing import List, Optional

from football_agent.output.market_display import format_market_pick, market_label
from football_agent.output.match_context_display import (
    format_data_sources_line,
    odds_available,
    odds_link_warning,
    openclaw_link_warning,
)
from football_agent.services.enrichment_contract import SOURCE_SKIPPED_NOT_CONFIGURED
from football_agent.domain.competition_context import COMPETITION_CONTEXT_LABELS_RU
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.source_completeness import format_telegram_completeness_hint

_FLAG_LABELS_RU = {
    "high_motivation": "высокая мотивация",
    "poor_form": "слабая форма",
    "strong_form": "сильная форма",
    "new_coach": "новый тренер",
    "coach_bounce_window": "эффект смены тренера",
    "thin_squad": "тонкий состав",
    "schedule_risk": "риск ротации / плотный календарь",
    "low_confidence_data": "мало данных для уверенного вывода",
    "home_side": "домашнее преимущество",
}


def _flag_label(flag: str) -> str:
    return _FLAG_LABELS_RU.get(flag, flag.replace("_", " "))


def _collect_scorer_factors(result: LivePipelineResult) -> List[str]:
    scored = result.scored_run
    if scored is None:
        return []

    pred = scored.prediction
    factors: List[str] = []

    for flag in pred.home_scoring.summary_flags:
        if flag == "home_side":
            continue
        label = _flag_label(flag)
        home = pred.match_meta.home_team.short_name or pred.match_meta.home_team.name
        factors.append(f"{home}: {label}")

    for flag in pred.away_scoring.summary_flags:
        away = pred.match_meta.away_team.short_name or pred.match_meta.away_team.name
        factors.append(f"{away}: {_flag_label(flag)}")

    if pred.express_safety and pred.express_safety.reasons:
        for reason in pred.express_safety.reasons[:2]:
            if reason not in factors:
                factors.append(reason)

    return factors


def _merge_key_factors(result: LivePipelineResult, *, max_items: int = 4) -> List[str]:
    """Prefer OpenClaw highlights when present; fill remainder with scorer flags."""
    items: List[str] = list(result.context_highlights)
    for factor in _collect_scorer_factors(result):
        if len(items) >= max_items:
            break
        if factor not in items:
            items.append(factor)
    return items[:max_items]


def _format_edge_line(result: LivePipelineResult) -> Optional[str]:
    scored = result.scored_run
    if scored is None or not odds_available(result.sources, result.odds_link_strategy):
        return None
    bm = scored.prediction.best_market
    if bm is None or bm.book_odds is None:
        return None
    if bm.edge is not None:
        pct = float(bm.edge) * 100.0
        sign = "+" if pct > 0 else ""
        return f"📈 Edge vs линия: {sign}{pct:.1f}%"
    return "📈 Линия букмекера учтена в расчёте"


def _data_warning_line(result: LivePipelineResult) -> str | None:
    scored = result.scored_run
    if scored is None:
        return None

    rep = scored.build_report
    parts: List[str] = []
    has_odds = odds_available(result.sources, result.odds_link_strategy)

    oc_status = result.sources.get("openclaw")
    if oc_status == "failed":
        parts.append("контекст OpenClaw недоступен")
    elif oc_status == SOURCE_SKIPPED_NOT_CONFIGURED:
        parts.append("OpenClaw не подключён")
    elif oc_status == "skipped":
        parts.append("анализ без OpenClaw")
    elif oc_status == "partial":
        parts.append("контекст OpenClaw неполный")

    link_warn = openclaw_link_warning(result.openclaw_link_strategy)
    if link_warn:
        parts.append(link_warn)

    odds_status = result.sources.get("odds")
    if odds_status == "failed":
        parts.append("линия букмекера недоступна")
    elif odds_status == SOURCE_SKIPPED_NOT_CONFIGURED:
        if result.odds_source == "openclaw" and oc_status in ("ok", "partial"):
            parts.append("линия не пришла от OpenClaw")
        elif result.enrichment_mode != "not_configured":
            pass
        else:
            parts.append("линия не настроена")
    elif odds_status == "skipped":
        parts.append("анализ без линии букмекера")
    elif not has_odds:
        odds_warn = odds_link_warning(result.odds_link_strategy)
        if odds_warn:
            parts.append(odds_warn)

    if rep.merge_missing_blocks:
        missing = [
            b
            for b in rep.merge_missing_blocks
            if b not in ("openclaw_context", "odds_context") or (b == "odds_context" and not has_odds)
        ]
        if missing:
            parts.append("нет блоков: " + ", ".join(missing[:2]))

    if not parts and result.warnings:
        parts.append(result.warnings[0][:80])

    if not parts:
        return None
    return "Данные неполные — " + "; ".join(parts)


def format_telegram_match_reply(result: LivePipelineResult) -> str:
    """User-facing compact analysis message."""
    scored = result.scored_run
    if scored is None:
        return result.user_message or "Анализ недоступен."

    snap = scored.snapshot
    pred = scored.prediction
    meta = snap.match_meta
    has_odds = odds_available(result.sources, result.odds_link_strategy)

    home = meta.home_team.short_name or meta.home_team.name
    away = meta.away_team.short_name or meta.away_team.name
    comp = meta.competition_name or meta.competition_code or "—"
    comp_suffix = _competition_context_suffix(result)

    lines = [
        f"⚽ {home} — {away}",
        f"🏆 {comp}{comp_suffix}",
    ]

    sources_line = format_data_sources_line(
        result.sources,
        odds_source=result.odds_source,
        enrichment_backend=result.enrichment_backend,
    )
    if sources_line:
        lines.append(sources_line)

    if meta.match_date_utc:
        lines.append(f"🕐 {meta.match_date_utc}")

    if pred.best_market:
        bm = pred.best_market
        pick = format_market_pick(
            bm.market_key,
            bm.probability,
            bm.book_odds,
            label=bm.label or market_label(bm.market_key),
        )
        lines.append(f"📊 Лучший рынок: {pick}")
    else:
        lines.append("📊 Лучший рынок: —")

    conf = pred.overall_confidence_score
    lines.append(f"🎯 Уверенность: {conf:.0%}")

    edge_line = _format_edge_line(result)
    if edge_line:
        lines.append(edge_line)

    lean = _build_lean(
        pred.best_market.market_key if pred.best_market else None,
        conf,
        has_odds=has_odds,
    )
    if lean:
        lines.append(f"💡 {lean}")

    factors = _merge_key_factors(result)
    if factors:
        lines.append("")
        lines.append("Ключевое:")
        for factor in factors:
            lines.append(f"• {factor}")

    warning = _data_warning_line(result)
    if warning:
        lines.append("")
        lines.append(f"⚠️ {warning}")

    if result.completeness:
        hint = format_telegram_completeness_hint(result.completeness)
        if hint and not warning:
            lines.append("")
            lines.append(f"ℹ️ {hint}")

    guard = result.competition_guardrail
    if guard and guard.guardrail_applied and guard.telegram_hint and not warning:
        lines.append("")
        lines.append(f"ℹ️ {guard.telegram_hint}")

    return "\n".join(lines)


def _competition_context_suffix(result: LivePipelineResult) -> str:
    clf = result.competition_classification
    if clf is None or clf.is_league:
        return ""
    label = COMPETITION_CONTEXT_LABELS_RU.get(clf.category, clf.category.value)
    return f" ({label})"


def _build_lean(best_market_key: str | None, confidence: float, *, has_odds: bool) -> str | None:
    if not best_market_key:
        return None
    label = market_label(best_market_key)
    if has_odds:
        if confidence >= 0.65:
            return f"Склонность к «{label}» с учётом линии букмекера."
        if confidence >= 0.5:
            return f"Умеренный сигнал на «{label}» относительно линии."
        return f"Слабый сигнал; «{label}» — с осторожностью."
    if confidence >= 0.65:
        return f"Склонность к «{label}» без линии — оценка по фактам."
    if confidence >= 0.5:
        return f"Умеренный сигнал на «{label}», линия не использована."
    return f"Слабый сигнал; «{label}» — без линии букмекера."
