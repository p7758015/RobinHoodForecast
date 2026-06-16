"""Flashscore odds collector tests (Odds A)."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.flashscore.odds_collector import FlashscoreOddsCollector
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _base_raw() -> dict:
    data = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_odds_missing_when_no_block() -> None:
    raw = _base_raw()
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.status == "missing"
    assert result.confidence == 0.0
    assert "odds_empty" in result.warnings
    assert result.payload["market_count"] == 0


def test_odds_partial_1x2_only() -> None:
    raw = _base_raw()
    raw["odds"] = {
        "bookmaker_name": "Example",
        "markets": {
            "1": 1.85,
            "X": 3.4,
            "2": 4.5,
        },
    }
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.status in ("partial", "ok")
    assert result.payload["market_count"] == 3
    markets = result.payload["markets"]
    assert markets["HOME_WIN"]["value"] == 1.85
    assert markets["DRAW"]["raw_label"] == "X"
    assert markets["AWAY_WIN"]["value"] == 4.5
    assert result.payload["derived_market_count"] == 2
    assert markets["HOME_OR_DRAW"]["derived"] is True


def test_odds_ok_full_coverage() -> None:
    raw = _base_raw()
    raw["odds"] = {
        "bookmaker_name": "Flashscore",
        "markets": {
            "home_win": 1.85,
            "draw": 3.4,
            "away_win": 4.5,
            "double_chance_1x": 1.22,
            "double_chance_x2": 1.95,
            "over_1_5": 1.3,
            "under_3_5": 1.55,
            "btts_yes": 1.8,
        },
    }
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.status == "ok"
    assert result.payload["market_count"] == 8
    assert result.confidence >= 0.85


def test_odds_invalid_values_ignored() -> None:
    raw = _base_raw()
    raw["odds"] = {
        "markets": {
            "1": 1.0,
            "X": "n/a",
            "2": -2.0,
            "btts_yes": 1.75,
        },
    }
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.payload["market_count"] == 1
    assert "BTTS_YES" in result.payload["markets"]
    assert any("odds_invalid_value" in w for w in result.warnings)


def test_odds_top_level_markets_shape() -> None:
    raw = _base_raw()
    raw["markets"] = {
        "home_win": 2.1,
        "away_win": 3.2,
        "btts_yes": 1.65,
    }
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.payload["market_count"] == 3
    assert result.payload["raw_snapshot_available"] is True


def test_odds_scraper_http_payload_fixture() -> None:
    """Scraper-shaped raw (odds.markets + odds_raw) from live oce capture."""
    sample = json.loads((_FIXTURES / "scraper_odds_sample.json").read_text(encoding="utf-8"))
    raw = enrich_http_flashscore_raw(sample)
    result = FlashscoreOddsCollector().collect(raw, MatchRef())
    assert result.status == "ok"
    assert result.payload["market_count"] == 8
    assert result.payload["bookmaker"] == "BetMGM.us"
    assert "odds_empty" not in result.warnings
    assert result.payload["markets"]["HOME_WIN"]["value"] == 1.4
