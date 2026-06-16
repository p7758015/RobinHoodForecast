"""Compact Telegram reply for multi-match league analysis."""

from __future__ import annotations

from typing import List, Optional

from football_agent.output.market_display import format_market_pick, market_label
from football_agent.output.telegram_match_output import _merge_key_factors
from football_agent.services.live_flashscore_pipeline import LivePipelineResult


def format_teleague_league_reply(
    *,
    competition_name: str,
    competition_country: Optional[str],
    date_from: str,
    date_to: str,
    results: List[LivePipelineResult],
    period_note: Optional[str] = None,
    truncated: bool = False,
    max_shown: int = 5,
) -> str:
    comp_line = competition_name
    if competition_country:
        comp_line = f"{comp_line} ({competition_country})"

    period = date_from if date_from == date_to else f"{date_from} — {date_to}"
    lines: List[str] = [
        f"Лига: {comp_line}",
        f"Период: {period}",
    ]
    if period_note:
        lines.append(f"({period_note})")

    ok = [r for r in results if r.success]
    parked = _count_parked(ok)
    predicted = len(ok) - parked

    if not ok:
        lines.append("")
        lines.append("Не удалось получить анализ ни по одному матчу.")
        return "\n".join(lines)

    lines.append("")
    for idx, result in enumerate(ok[:max_shown], start=1):
        lines.append(_format_league_match_block(result, index=idx))

    if truncated or len(ok) > max_shown:
        lines.append(f"(показаны первые {min(max_shown, len(ok))} из {len(ok)} матчей)")

    lines.append("")
    lines.append(_format_league_summary(ok, predicted=predicted, parked=parked))
    lines.append(_format_risk_footer(ok))
    lines.append("")
    lines.append(
        "Уточнить период: «дай прогноз на лигу … на 2026-06-15» "
        "или «… на 2026-06-15 — 2026-06-20»"
    )
    return "\n".join(lines)


def _format_league_match_block(result: LivePipelineResult, *, index: int) -> str:
    scored = result.scored_run
    if scored is None:
        return f"{index}. Анализ недоступен"

    snap = scored.snapshot
    pred = scored.prediction
    meta = snap.match_meta
    home = meta.home_team.short_name or meta.home_team.name
    away = meta.away_team.short_name or meta.away_team.name
    date_part = f" · {meta.match_date_utc}" if meta.match_date_utc else ""

    is_parked = scored.scoring_skipped or pred.analysis_mode == "analysis_only"
    mode = "analysis-only" if is_parked else "league prediction"

    block: List[str] = [f"{index}. {home} — {away}{date_part}", f"   Тип: {mode}"]

    if is_parked:
        parked = pred.parked_context
        if parked is not None:
            block.append(f"   Почему: {parked.reason}")
        elif pred.prediction_summary:
            block.append(f"   {pred.prediction_summary[:120]}")
        block.append("   Прогноз: кодовый прогноз не сформирован")
    elif pred.best_market:
        bm = pred.best_market
        pick = format_market_pick(
            bm.market_key,
            bm.probability,
            bm.book_odds,
            label=bm.label or market_label(bm.market_key),
        )
        block.append(f"   Рынок: {pick}")
    else:
        block.append("   Прогноз: кодовый прогноз не сформирован")

    conf = pred.overall_confidence_score
    block.append(f"   Уверенность: {conf:.0%} — {_confidence_label(conf, has_prediction=not is_parked)}")

    factors = _merge_key_factors(result, max_items=4)
    if factors:
        for factor in factors[:4]:
            block.append(f"   • {factor}")

    return "\n".join(block)


def _confidence_label(conf: float, *, has_prediction: bool) -> str:
    if not has_prediction:
        return "данные для контекста"
    if conf >= 0.65:
        return "выше среднего"
    if conf >= 0.5:
        return "умеренная"
    return "осторожно"


def _count_parked(results: List[LivePipelineResult]) -> int:
    n = 0
    for r in results:
        scored = r.scored_run
        if scored and (scored.scoring_skipped or scored.prediction.analysis_mode == "analysis_only"):
            n += 1
    return n


def _format_league_summary(
    results: List[LivePipelineResult],
    *,
    predicted: int,
    parked: int,
) -> str:
    parts = [f"Итого: {len(results)} матч(ей)"]
    if predicted:
        parts.append(f"прогнозов {predicted}")
    if parked:
        parts.append(f"analysis-only {parked}")
    return " · ".join(parts)


def _format_risk_footer(results: List[LivePipelineResult]) -> str:
    risks: List[str] = []
    no_odds = 0
    low_conf = 0
    incomplete = 0

    for r in results:
        scored = r.scored_run
        if scored is None:
            continue
        if r.sources.get("odds") in ("failed", "skipped", None) and r.sources.get("odds") != "ok":
            no_odds += 1
        if scored.prediction.overall_confidence_score < 0.5:
            low_conf += 1
        if r.warnings:
            incomplete += 1

    if no_odds:
        risks.append(f"без линии: {no_odds}")
    if low_conf:
        risks.append(f"низкая уверенность: {low_conf}")
    if incomplete:
        risks.append(f"неполные данные: {incomplete}")

    if not risks:
        return "Риски по выборке: нет явных красных флагов"
    return "Риски по выборке: " + "; ".join(risks)
