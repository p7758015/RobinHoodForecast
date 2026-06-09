"""Deterministic Russian user-facing text for v2 pipeline (no LLM required)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.domain.models_v2 import ExpressBetV2, MatchPredictionResultV2
from football_agent.output.market_display import format_market_pick, market_label, market_pick_from_dict

LEAGUE_FLAG = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "PD": "🇪🇸",
    "SA": "🇮🇹",
    "BL1": "🇩🇪",
    "FL1": "🇫🇷",
}


def _flag(code: str) -> str:
    return LEAGUE_FLAG.get(code, "🏆")


def _match_title(meta: Dict[str, Any]) -> str:
    return f"{meta.get('home', '?')} — {meta.get('away', '?')}"


def format_v2_match_payload_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich payload with display lines for LLM or direct print."""
    bm = payload.get("best_market")
    payload = dict(payload)
    payload["best_pick_line"] = market_pick_from_dict(bm)
    top = []
    for m in payload.get("top_markets") or []:
        if m.get("book_odds") is None and (m.get("probability") or 0) < 0.55:
            continue
        top.append(
            {
                **m,
                "display_line": market_pick_from_dict(m),
                "market_label": market_label(m.get("market_key", "")),
            }
        )
    payload["top_picks"] = top[:3]
    return payload


def format_v2_single_match_text(payload: Dict[str, Any]) -> str:
    payload = format_v2_match_payload_dict(payload)
    meta = payload.get("match") or {}
    lines = [
        f"{_flag(meta.get('competition', ''))} {_match_title(meta)}",
        f"Лучший рынок: {payload.get('best_pick_line', '—')}",
    ]
    for i, pick in enumerate(payload.get("top_picks") or [], start=1):
        if pick.get("market_key") == (payload.get("best_market") or {}).get("market_key"):
            continue
        lines.append(f"  {i}. {pick.get('display_line', '—')}")
    conf = payload.get("confidence")
    if conf is not None:
        lines.append(f"Уверенность данных: {float(conf):.0%}")
    return "\n".join(lines)


def format_v2_all_matches_text(payload: Dict[str, Any]) -> str:
    date_str = payload.get("date", "")
    matches = payload.get("matches") or []
    if not matches:
        return f"На {date_str} матчей не найдено."

    lines = [f"Прогнозы на {date_str} ({len(matches)} матчей):", ""]
    for item in matches:
        enriched = format_v2_match_payload_dict(item)
        meta = enriched.get("match") or {}
        lines.append(
            f"{_flag(meta.get('competition', ''))} {_match_title(meta)} — "
            f"{enriched.get('best_pick_line', '—')}"
        )
    lines.append("")
    lines.append("Экспресс не собран (только по явному запросу).")
    return "\n".join(lines)


def format_v2_express_text(payload: Dict[str, Any]) -> str:
    events = payload.get("events") or []
    lines = [
        f"Экспресс (цель кф {payload.get('target_odds', '—')}):",
        f"Итого: кф {payload.get('total_odds', '—')}, "
        f"вероятность {float(payload.get('total_probability', 0)):.0%}",
        "",
    ]
    for i, ev in enumerate(events, start=1):
        m = ev.get("match") or {}
        mk = ev.get("market") or {}
        pick = format_market_pick(
            mk.get("market_key", ""),
            float(mk.get("probability", 0)),
            mk.get("book_odds"),
            label=mk.get("label") or "",
        )
        lines.append(f"{i}. {m.get('home', '?')} — {m.get('away', '?')}: {pick}")
    note = payload.get("selection_notes")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def build_match_payload_from_result(result: MatchPredictionResultV2) -> Dict[str, Any]:
    meta = result.match_meta
    bm = result.best_market
    ranked = sorted(result.market_predictions, key=lambda m: m.probability, reverse=True)
    top = []
    for m in ranked[:5]:
        top.append(
            {
                "market_key": m.market_key,
                "probability": m.probability,
                "book_odds": m.book_odds,
                "label": m.label or market_label(m.market_key),
            }
        )
    payload = {
        "pipeline_version": "v2",
        "match": {
            "competition": meta.competition_code,
            "home": meta.home_team.short_name or meta.home_team.name,
            "away": meta.away_team.short_name or meta.away_team.name,
        },
        "best_market": (
            {
                "market_key": bm.market_key,
                "probability": bm.probability,
                "book_odds": bm.book_odds,
                "label": bm.label or market_label(bm.market_key),
            }
            if bm
            else None
        ),
        "top_markets": top,
        "confidence": result.overall_confidence_score,
    }
    return format_v2_match_payload_dict(payload)
