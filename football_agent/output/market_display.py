"""Human-readable market labels and pick formatting (v2 output layer)."""

from __future__ import annotations

from typing import Any, Dict, Optional

# Single source for user-facing market names (8 first-batch markets)
MARKET_DISPLAY_RU: Dict[str, str] = {
    "HOME_WIN": "П1",
    "AWAY_WIN": "П2",
    "HOME_NOT_LOSE": "1X",
    "AWAY_NOT_LOSE": "X2",
    "BTTS_YES": "Обе забьют",
    "HOME_TEAM_TO_SCORE": "К1 забьёт",
    "AWAY_TEAM_TO_SCORE": "К2 забьёт",
    "OVER_1_5": "ТБ 1.5",
}


def market_label(market_key: str, fallback: str = "") -> str:
    return MARKET_DISPLAY_RU.get(market_key, fallback or market_key)


def _format_probability(probability: float) -> str:
    try:
        p = float(probability)
    except (TypeError, ValueError):
        return "н/д"
    if p > 1.0:
        p = p / 100.0
    p = max(0.0, min(1.0, p))
    return f"{round(p * 100)}%"


def _format_book_odds(book_odds: Optional[float]) -> str:
    if book_odds is None:
        return "кф н/д"
    try:
        o = float(book_odds)
    except (TypeError, ValueError):
        return "кф н/д"
    if o <= 1.0:
        return "кф н/д"
    return f"кф {o:.2f}"


def format_market_pick(
    market_key: str,
    probability: float,
    book_odds: Optional[float] = None,
    *,
    label: str = "",
) -> str:
    lbl = (label or market_label(market_key) or "").strip() or market_label(market_key) or "—"
    return f"{lbl}, {_format_book_odds(book_odds)}, {_format_probability(probability)}"


def market_pick_from_dict(market: Optional[Dict[str, Any]]) -> str:
    if not market:
        return "—"
    return format_market_pick(
        market.get("market_key", ""),
        float(market.get("probability", 0)),
        market.get("book_odds"),
        label=market.get("label") or "",
    )
