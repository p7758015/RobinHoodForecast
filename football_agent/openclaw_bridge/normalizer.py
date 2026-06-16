"""Normalize live backend payloads into bridge contract shapes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.odds.service import MARKET_FIELDS
from football_agent.openclaw_bridge.backend_client import CONTEXT_BLOCK_KEYS

RELIABILITY_LEVELS = {"LOW", "MEDIUM", "HIGH"}
CONFIDENCE_LEVELS = {"LOW", "MEDIUM", "HIGH"}
AFFECTS_TEAM = {"HOME", "AWAY", "BOTH"}


def normalize_context_blocks(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in CONTEXT_BLOCK_KEYS:
        block = raw.get(key)
        if not isinstance(block, dict) or not block:
            continue
        if key == "news":
            normalized = _normalize_news(block)
        elif key == "squad_context":
            normalized = _normalize_squad(block)
        else:
            normalized = dict(block)
        if normalized:
            out[key] = normalized
    return out


def normalize_odds_markets(raw: Dict[str, Any]) -> Dict[str, Any]:
    markets = raw.get("markets") if isinstance(raw.get("markets"), dict) else raw
    if not isinstance(markets, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in MARKET_FIELDS:
        val = markets.get(key)
        if val is None:
            continue
        quote = _normalize_quote(val)
        if quote:
            out[key] = quote
    return out


def count_filled_markets(markets: Dict[str, Any]) -> int:
    return sum(1 for k in MARKET_FIELDS if markets.get(k))


def _normalize_news(block: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for key in ("match_news_items", "home_news_items", "away_news_items"):
        raw_items = block.get(key)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title and not summary:
                continue
            rel = str(item.get("reliability_level") or "LOW").upper()
            affects = str(item.get("affects_team") or "BOTH").upper()
            items.append(
                {
                    "title": title or "Match news",
                    "summary": summary or title,
                    "source_name": str(item.get("source_name") or "openclaw_bridge"),
                    "reliability_level": rel if rel in RELIABILITY_LEVELS else "LOW",
                    "affects_team": affects if affects in AFFECTS_TEAM else "BOTH",
                },
            )
    return {"match_news_items": items} if items else {}


def _normalize_squad(block: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for side in ("home", "away"):
        team = block.get(side)
        if not isinstance(team, dict):
            continue
        side_out = dict(team)
        for list_key in ("missing_players_context", "returning_players_context", "lineup_uncertainty_notes"):
            val = side_out.get(list_key)
            if isinstance(val, list):
                side_out[list_key] = [x for x in val if x]
        if side_out:
            out[side] = side_out
    return out


def _normalize_quote(val: Any) -> Optional[Dict[str, Any]]:
    if isinstance(val, (int, float)) and val > 1.0:
        return {"odds_value": float(val), "bookmaker_name": "openclaw_bridge", "confidence": "LOW"}
    if not isinstance(val, dict):
        return None
    odds = val.get("odds_value") or val.get("odd")
    try:
        odds_f = float(odds)
    except (TypeError, ValueError):
        return None
    if odds_f <= 1.0:
        return None
    conf = str(val.get("confidence") or "LOW").upper()
    return {
        "odds_value": odds_f,
        "bookmaker_name": str(val.get("bookmaker_name") or "openclaw_bridge"),
        "confidence": conf if conf in CONFIDENCE_LEVELS else "LOW",
    }
