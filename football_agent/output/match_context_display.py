"""
User-facing display helpers for merged match context (no scoring logic).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.services.enrichment_contract import SOURCE_SKIPPED_NOT_CONFIGURED


def format_data_sources_line(
    sources: Dict[str, str],
    *,
    odds_source: Optional[str] = None,
    enrichment_backend: Optional[str] = None,
) -> str:
    """Compact provenance line for Telegram / CLI."""
    parts: List[str] = []
    if sources.get("flashscore") == "ok":
        parts.append("Flashscore")

    oc = sources.get("openclaw")
    od = sources.get("odds")
    same_backend = (
        odds_source == "openclaw"
        and enrichment_backend in ("openclaw", "openclaw_unified")
        and oc in ("ok", "partial")
        and od in ("ok", "partial")
    )

    if same_backend:
        parts.append("OpenClaw (контекст + линия)")
    else:
        if oc == "ok":
            parts.append("OpenClaw")
        elif oc == "partial":
            parts.append("OpenClaw (частично)")
        elif oc == "failed":
            parts.append("OpenClaw недоступен")
        elif oc == SOURCE_SKIPPED_NOT_CONFIGURED:
            parts.append("OpenClaw не подключён")
        elif oc == "skipped":
            parts.append("без OpenClaw")

        if od == "ok":
            if odds_source == "separate_service":
                parts.append("линия (отд. сервис)")
            else:
                parts.append("линия")
        elif od == "partial":
            parts.append("линия (частично)")
        elif od == "failed":
            parts.append("линия недоступна")
        elif od == SOURCE_SKIPPED_NOT_CONFIGURED:
            if oc in ("ok", "partial") and odds_source == "openclaw":
                parts.append("линия не в ответе OpenClaw")
            elif oc not in (SOURCE_SKIPPED_NOT_CONFIGURED, "skipped", None):
                pass
            else:
                parts.append("линия не настроена")
        elif od == "skipped":
            parts.append("без линии")

    if not parts:
        return ""
    return "📡 Источники: " + " + ".join(parts)


def extract_openclaw_highlights(
    ctx: Optional[OpenClawMatchContext],
    *,
    max_items: int = 4,
) -> List[str]:
    """Short human-readable bullets from OpenClaw context blocks."""
    if ctx is None:
        return []

    items: List[str] = []

    if ctx.squad_context:
        for label, side in (("Хозяева", ctx.squad_context.home), ("Гости", ctx.squad_context.away)):
            for player in side.missing_players_context[:1]:
                reason = player.reason or player.status
                items.append(f"{label}: отсутствует {player.player_name} ({reason})")
            for player in side.returning_players_context[:1]:
                items.append(f"{label}: возвращается {player.player_name}")
            for note in (side.injury_notes or [])[:1]:
                items.append(f"{label}: {note}")
            for note in (side.expected_rotation_notes or [])[:1]:
                items.append(f"{label}: {note}")

    if ctx.motivation_narrative:
        for label, side in (
            ("Хозяева", ctx.motivation_narrative.home),
            ("Гости", ctx.motivation_narrative.away),
        ):
            for field_name in ("pressure_summary", "must_win_narrative", "primary_objective_summary"):
                text = getattr(side, field_name, None)
                if text:
                    items.append(f"{label}: {text}")
                    break
        matchwide = ctx.motivation_narrative.matchwide
        if matchwide.public_narrative_summary:
            items.append(f"Контекст: {matchwide.public_narrative_summary}")

    if ctx.news:
        for item in (ctx.news.match_news_items or [])[:1]:
            if item.title:
                items.append(f"Новости: {item.title}")
        if not ctx.news.match_news_items:
            for item in (ctx.news.home_news_items or [])[:1]:
                if item.title:
                    items.append(f"Новости (дом): {item.title}")
                    break

    if ctx.fatigue_schedule_context:
        for label, side in (
            ("Хозяева", ctx.fatigue_schedule_context.home),
            ("Гости", ctx.fatigue_schedule_context.away),
        ):
            if side.fatigue_summary:
                items.append(f"{label}: {side.fatigue_summary}")
            elif side.rotation_expectation_summary:
                items.append(f"{label}: {side.rotation_expectation_summary}")

    if ctx.coach_context:
        for label, side in (("Хозяева", ctx.coach_context.home), ("Гости", ctx.coach_context.away)):
            if side.recent_change_flag and side.coach_name:
                items.append(f"{label}: новый тренер ({side.coach_name})")
            elif side.pressure_summary:
                items.append(f"{label}: {side.pressure_summary}")

    seen = set()
    unique: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique[:max_items]


def openclaw_link_warning(link_strategy: Optional[str]) -> Optional[str]:
    if link_strategy in ("provided_without_link", "unlinked"):
        return "контекст OpenClaw не привязан к матчу"
    return None


def odds_link_warning(link_strategy: Optional[str]) -> Optional[str]:
    if link_strategy in ("provided_without_link", "unlinked"):
        return "линия букмекера не привязана к матчу"
    return None


def odds_available(sources: Dict[str, str], link_strategy: Optional[str]) -> bool:
    return sources.get("odds") in ("ok", "partial") and link_strategy not in (
        None,
        "unlinked",
        "provided_without_link",
        "none",
    )
