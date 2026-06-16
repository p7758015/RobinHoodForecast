"""Deterministic per-block confidence (match_meta, teams, form, odds)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.collectors.contracts import BlockStatus


def clamp_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


def match_meta_confidence(
    *,
    home_team: str,
    away_team: str,
    competition_name: str,
    kickoff_present: bool,
    venue_present: bool,
    round_present: bool,
    competition_valid: bool,
    teams_valid: bool,
) -> tuple[float, BlockStatus, List[str]]:
    warnings: List[str] = []
    if not teams_valid:
        return 0.0, "failed", ["match_meta_invalid_teams"]
    if not competition_valid:
        warnings.append("match_meta_invalid_competition")
        return 0.15, "failed", warnings

    score = 1.0
    if not kickoff_present:
        score -= 0.15
        warnings.append("match_meta_missing_kickoff")
    if not venue_present:
        score -= 0.05
    if not round_present:
        score -= 0.05

    conf = clamp_confidence(score)
    status: BlockStatus = "ok" if conf >= 0.7 else "partial"
    return conf, status, warnings


def standings_confidence(payload: Dict[str, Any]) -> tuple[float, BlockStatus, List[str]]:
    warnings: List[str] = []
    sides = ("home", "away")
    fields = ("position", "points", "goal_difference", "matches_played")
    filled = 0
    total = 0
    for side in sides:
        for field in fields:
            total += 1
            key = f"{side}_{field}"
            val = payload.get(key)
            if val is not None and val != "":
                filled += 1

    if filled == 0:
        return 0.0, "missing", ["standings_empty"]

    ratio = filled / max(total, 1)
    conf = clamp_confidence(ratio)
    if conf >= 0.6:
        return conf, "ok", warnings
    if conf > 0:
        warnings.append("standings_partial_fields")
        return conf, "partial", warnings
    return 0.0, "missing", ["standings_no_signal"]


def _count_form_results(payload: Dict[str, Any], side: str) -> int:
    block = payload.get(side) or {}
    if not isinstance(block, dict):
        return 0
    results = block.get("last_n_results") or []
    return len(results) if isinstance(results, list) else 0


def form_confidence(payload: Dict[str, Any]) -> tuple[float, BlockStatus, List[str]]:
    warnings: List[str] = []
    home_n = _count_form_results(payload, "home")
    away_n = _count_form_results(payload, "away")
    max_n = max(home_n, away_n)
    min_n = min(home_n, away_n)

    if max_n == 0:
        return 0.0, "missing", ["form_empty"]

    if max_n < 3:
        warnings.append("form_insufficient_matches")
        return 0.25, "partial", warnings

    score = 0.5
    if max_n >= 5:
        score += 0.35
    elif max_n >= 3:
        score += 0.2

    if min_n >= 3:
        score += 0.1

    home_block = payload.get("home") or {}
    away_block = payload.get("away") or {}
    if isinstance(home_block, dict) and home_block.get("home_only_form"):
        score += 0.025
    if isinstance(away_block, dict) and away_block.get("away_only_form"):
        score += 0.025

    conf = clamp_confidence(score)
    if max_n >= 5 and min_n >= 3:
        return conf, "ok", warnings
    warnings.append("form_partial_coverage")
    return conf, "partial", warnings


def _real_odds_market_count(payload: Dict[str, Any], markets: Dict[str, Any]) -> int:
    """Real (non-derived) markets only — derived fills do not affect confidence."""
    if payload.get("market_count") is not None:
        return int(payload.get("market_count") or 0)
    return sum(
        1
        for entry in markets.values()
        if isinstance(entry, dict) and not entry.get("derived")
    )


def odds_confidence(payload: Dict[str, Any]) -> tuple[float, BlockStatus, List[str]]:
    warnings: List[str] = []
    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
    count = _real_odds_market_count(payload, markets)

    if count == 0:
        return 0.0, "missing", ["odds_empty"]

    has_1x2 = all(
        k in markets and isinstance(markets.get(k), dict) and not markets[k].get("derived")
        for k in ("HOME_WIN", "DRAW", "AWAY_WIN")
    )

    if count >= 5:
        score = 0.85
        status: BlockStatus = "ok"
    elif count >= 3:
        score = 0.55
        status = "partial"
        warnings.append("odds_partial_coverage")
    else:
        score = 0.3
        status = "partial"
        warnings.append("odds_low_coverage")

    if has_1x2:
        score += 0.1
    else:
        warnings.append("odds_missing_1x2_set")

    conf = clamp_confidence(score)
    if count >= 5 and has_1x2:
        return conf, "ok", warnings
    return conf, status, warnings


def bundle_overall_confidence(blocks: Dict[str, float]) -> float:
    if not blocks:
        return 0.0
    weights = {"match_meta": 0.4, "teams": 0.2, "form": 0.2, "odds": 0.2}
    total_w = sum(weights.get(k, 0.1) for k in blocks)
    score = sum(blocks.get(k, 0.0) * weights.get(k, 0.1) for k in blocks)
    return clamp_confidence(score / max(total_w, 0.01))
