"""Flashscore prematch odds block collector (Odds A)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from football_agent.collectors.confidence import odds_confidence
from football_agent.collectors.odds_derived import (
    apply_derived_double_chance_markets,
    count_real_markets,
)
from football_agent.collectors.contracts import (
    BLOCK_ODDS,
    ODDS_MARKET_KEYS,
    BlockCollectionResult,
    MatchRef,
    SourceAttempt,
    utc_now,
)

# Raw key aliases → canonical market keys (defensive, no derived fills).
_RAW_KEY_TO_CANONICAL: Dict[str, str] = {}
for _canonical in ODDS_MARKET_KEYS:
    _RAW_KEY_TO_CANONICAL[_canonical.lower()] = _canonical
    _RAW_KEY_TO_CANONICAL[_canonical] = _canonical

_RAW_KEY_TO_CANONICAL.update(
    {
        "home_win": "HOME_WIN",
        "1": "HOME_WIN",
        "home": "HOME_WIN",
        "draw": "DRAW",
        "x": "DRAW",
        "away_win": "AWAY_WIN",
        "2": "AWAY_WIN",
        "away": "AWAY_WIN",
        "double_chance_1x": "HOME_OR_DRAW",
        "1x": "HOME_OR_DRAW",
        "dc_1x": "HOME_OR_DRAW",
        "double_chance_x2": "AWAY_OR_DRAW",
        "x2": "AWAY_OR_DRAW",
        "dc_x2": "AWAY_OR_DRAW",
        "over_1_5": "OVER_1_5",
        "o1.5": "OVER_1_5",
        "o_1_5": "OVER_1_5",
        "under_3_5": "UNDER_3_5",
        "u3.5": "UNDER_3_5",
        "u_3_5": "UNDER_3_5",
        "btts_yes": "BTTS_YES",
        "btts": "BTTS_YES",
        "gg": "BTTS_YES",
        "both_teams_to_score_yes": "BTTS_YES",
        "home_team_to_score_yes": "HOME_TEAM_TO_SCORE_YES",
        "home_team_to_score": "HOME_TEAM_TO_SCORE_YES",
        "away_team_to_score_yes": "AWAY_TEAM_TO_SCORE_YES",
        "away_team_to_score": "AWAY_TEAM_TO_SCORE_YES",
    },
)


def _parse_odd_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f > 1.0 else None
    if isinstance(value, dict):
        for key in ("value", "odds_value", "odd", "price", "decimal"):
            if key in value:
                return _parse_odd_value(value[key])
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            f = float(text)
            return f if f > 1.0 else None
        except ValueError:
            return None
    return None


def _canonical_key(raw_key: str) -> Optional[str]:
    norm = (raw_key or "").strip()
    if not norm:
        return None
    return _RAW_KEY_TO_CANONICAL.get(norm) or _RAW_KEY_TO_CANONICAL.get(norm.lower())


def _looks_like_odds_markets(data: Dict[str, Any]) -> bool:
    if not data:
        return False
    for key in data:
        if _canonical_key(str(key)) is not None:
            return True
    return False


def _extract_raw_markets(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str], bool]:
    """
    Locate odds markets dict inside enriched Flashscore raw.

    Supported shapes:
    - raw["odds"]["markets"]
    - raw["odds"] flat market map
    - raw["markets"] at top level
    - raw["odds"]["bookmakers"][0]["markets"]
    """
    bookmaker: Optional[str] = None
    snapshot = False

    odds_block = raw.get("odds")
    if isinstance(odds_block, dict):
        snapshot = True
        bookmaker = (
            str(odds_block.get("bookmaker_name") or odds_block.get("bookmaker") or "").strip() or None
        )
        nested = odds_block.get("markets")
        if isinstance(nested, dict) and nested:
            return nested, bookmaker, snapshot
        if _looks_like_odds_markets(odds_block):
            return odds_block, bookmaker, snapshot
        bookmakers = odds_block.get("bookmakers")
        if isinstance(bookmakers, list):
            for item in bookmakers:
                if not isinstance(item, dict):
                    continue
                mk = item.get("markets")
                if isinstance(mk, dict) and mk:
                    bm = str(item.get("name") or item.get("bookmaker") or bookmaker or "").strip() or None
                    return mk, bm, snapshot

    top_markets = raw.get("markets")
    if isinstance(top_markets, dict) and _looks_like_odds_markets(top_markets):
        snapshot = True
        return top_markets, bookmaker, snapshot

    return {}, bookmaker, snapshot


def normalize_flashscore_odds_markets(
    raw_markets: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Map raw market keys to canonical Odds A payload entries."""
    normalized: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []

    for raw_key, raw_val in raw_markets.items():
        canonical = _canonical_key(str(raw_key))
        if not canonical:
            continue
        if canonical in normalized:
            warnings.append(f"odds_duplicate_market:{canonical}")
            continue
        value = _parse_odd_value(raw_val)
        if value is None:
            warnings.append(f"odds_invalid_value:{raw_key}")
            continue
        normalized[canonical] = {
            "value": value,
            "raw_label": str(raw_key),
        }

    return normalized, warnings


class FlashscoreOddsCollector:
    """Collect prematch odds from Flashscore enriched raw JSON."""

    BLOCK = BLOCK_ODDS
    SOURCE = "flashscore"

    def collect(self, raw: Dict[str, Any], ref: MatchRef) -> BlockCollectionResult:
        started = utc_now()
        warnings: List[str] = []

        raw_markets, bookmaker, snapshot_available = _extract_raw_markets(raw)
        if not raw_markets:
            finished = utc_now()
            return BlockCollectionResult(
                block=self.BLOCK,
                status="missing",
                confidence=0.0,
                source=self.SOURCE,
                collected_at_utc=finished,
                payload={
                    "bookmaker": "flashscore",
                    "markets": {},
                    "market_count": 0,
                    "raw_snapshot_available": snapshot_available,
                },
                warnings=["odds_empty"],
                attempts=[
                    SourceAttempt(
                        block=self.BLOCK,
                        source=self.SOURCE,
                        started_at_utc=started,
                        finished_at_utc=finished,
                        status="missing",
                        warnings=["odds_empty"],
                        raw_ref=raw.get("_collector_raw_ref"),
                        duration_ms=int((finished - started).total_seconds() * 1000),
                    ),
                ],
                raw_ref=raw.get("_collector_raw_ref"),
            )

        markets, parse_warnings = normalize_flashscore_odds_markets(raw_markets)
        warnings.extend(parse_warnings)

        real_market_count = count_real_markets(markets)
        markets, derived_warnings = apply_derived_double_chance_markets(markets)
        warnings.extend(derived_warnings)

        derived_market_count = len(markets) - real_market_count

        payload: Dict[str, Any] = {
            "bookmaker": bookmaker or "flashscore",
            "markets": markets,
            "market_count": real_market_count,
            "derived_market_count": derived_market_count,
            "raw_snapshot_available": snapshot_available,
        }

        confidence, status, conf_warnings = odds_confidence(payload)
        warnings.extend(conf_warnings)

        finished = utc_now()
        return BlockCollectionResult(
            block=self.BLOCK,
            status=status,
            confidence=confidence,
            source=self.SOURCE,
            collected_at_utc=finished,
            payload=payload,
            warnings=warnings,
            attempts=[
                SourceAttempt(
                    block=self.BLOCK,
                    source=self.SOURCE,
                    started_at_utc=started,
                    finished_at_utc=finished,
                    status=status,  # type: ignore[arg-type]
                    warnings=list(warnings),
                    raw_ref=raw.get("_collector_raw_ref"),
                    duration_ms=int((finished - started).total_seconds() * 1000),
                ),
            ],
            raw_ref=raw.get("_collector_raw_ref"),
        )
