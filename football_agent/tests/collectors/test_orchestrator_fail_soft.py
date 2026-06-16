"""Orchestrator fail-soft tests."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _raw(name: str) -> dict:
    data = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_orchestrator_ok_valid_match() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    bundle, trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert not bundle.aborted
    assert bundle.overall_status in ("ok", "partial")
    assert "match_meta" in bundle.blocks
    assert trace.block_status["match_meta"] in ("ok", "partial")


def test_orchestrator_aborts_on_invalid_meta() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["home_team_name"] = "Unknown"
    raw["away_team_name"] = "Unknown"
    bundle, trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert bundle.aborted
    assert bundle.abort_reason == "match_meta_failed"
    assert "teams" not in bundle.blocks or bundle.blocks.get("teams") is None


def test_orchestrator_continues_when_standings_missing() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["standings"] = {}
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert not bundle.aborted
    assert bundle.blocks["teams"].status == "missing"
    assert "form" in bundle.blocks


def test_orchestrator_continues_when_form_partial() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["form"] = {
        "home": {"last_n_results": ["W", "D", "L"]},
        "away": {"last_n_results": []},
    }
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert not bundle.aborted
    assert bundle.blocks["form"].status == "partial"


def test_orchestrator_continues_when_odds_missing() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    bundle, trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert not bundle.aborted
    assert "odds" in bundle.blocks
    assert bundle.blocks["odds"].status == "missing"
    assert trace.block_status["odds"] == "missing"


def test_orchestrator_includes_odds_when_present() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["odds"] = {
        "markets": {"home_win": 1.9, "draw": 3.3, "away_win": 4.1, "btts_yes": 1.7},
    }
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert not bundle.aborted
    assert bundle.blocks["odds"].payload["market_count"] == 4


def test_orchestrator_aborts_before_odds_on_failed_meta() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["home_team_name"] = ""
    raw["away_team_name"] = ""
    bundle, _trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    assert bundle.aborted
    assert "odds" not in bundle.blocks
