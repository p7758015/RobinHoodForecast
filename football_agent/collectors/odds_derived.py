"""Derived prematch odds from collector base markets (Odds C-lite)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# No bookmaker margin simulation on this phase — transparent probability sum only.
DERIVED_MARGIN_ADJUSTMENT = 0.0

DOUBLE_CHANCE_DERIVED_SPECS: dict[str, tuple[str, str, str]] = {
    "HOME_OR_DRAW": ("HOME_WIN", "DRAW", "1X"),
    "AWAY_OR_DRAW": ("AWAY_WIN", "DRAW", "X2"),
}


def derive_double_chance_price(
    odds_leg: float,
    odds_draw: float,
    *,
    margin_adjustment: float = DERIVED_MARGIN_ADJUSTMENT,
) -> Optional[float]:
    """
    Derive a double-chance decimal price from two mutually exclusive legs.

    Implied probabilities are summed without margin recovery:
        p_combined = 1/odds_leg + 1/odds_draw
        price = (1 / p_combined) * (1 - margin_adjustment)

    Returns None when inputs are invalid or the result would be <= 1.0.
    """
    if odds_leg <= 1.0 or odds_draw <= 1.0:
        return None
    p_combined = (1.0 / odds_leg) + (1.0 / odds_draw)
    if p_combined <= 0.0:
        return None
    price = (1.0 / p_combined) * (1.0 - margin_adjustment)
    if price <= 1.0:
        return None
    return round(price, 2)


def _real_market_value(markets: Dict[str, Dict[str, Any]], key: str) -> Optional[float]:
    entry = markets.get(key)
    if not isinstance(entry, dict) or entry.get("derived"):
        return None
    value = entry.get("value")
    try:
        odds_value = float(value)
    except (TypeError, ValueError):
        return None
    return odds_value if odds_value > 1.0 else None


def _has_real_market(markets: Dict[str, Dict[str, Any]], key: str) -> bool:
    entry = markets.get(key)
    return isinstance(entry, dict) and not entry.get("derived")


def apply_derived_double_chance_markets(
    markets: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Add derived HOME_OR_DRAW / AWAY_OR_DRAW when base legs exist.

    Never overwrites an existing real market entry.
    """
    result: Dict[str, Dict[str, Any]] = dict(markets)
    warnings: List[str] = []

    for target, (leg_a, leg_draw, raw_label) in DOUBLE_CHANCE_DERIVED_SPECS.items():
        if _has_real_market(result, target):
            continue

        leg_a_present = leg_a in result
        leg_draw_present = leg_draw in result
        if not leg_a_present or not leg_draw_present:
            continue

        val_a = _real_market_value(result, leg_a)
        val_draw = _real_market_value(result, leg_draw)
        if val_a is None or val_draw is None:
            warnings.append(f"odds_derived_failed:{target}_invalid_source")
            continue

        derived_price = derive_double_chance_price(val_a, val_draw)
        if derived_price is None:
            warnings.append(f"odds_derived_failed:{target}_invalid_source")
            continue

        result[target] = {
            "value": derived_price,
            "raw_label": raw_label,
            "derived": True,
            "derived_from": [leg_a, leg_draw],
        }
        warnings.append(f"odds_derived_created:{target}")

    return result, warnings


def count_real_markets(markets: Dict[str, Any]) -> int:
    """Count non-derived market entries (used for confidence / coverage)."""
    if not isinstance(markets, dict):
        return 0
    return sum(
        1
        for entry in markets.values()
        if isinstance(entry, dict) and not entry.get("derived")
    )
