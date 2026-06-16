"""StandingsCollector tests."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.flashscore.standings_collector import StandingsCollector
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _raw(name: str) -> dict:
    data = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_standings_ok_from_fixture() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    result = StandingsCollector().collect(raw, MatchRef())
    assert result.status in ("ok", "partial")
    assert result.payload.get("home_position") is not None


def test_standings_missing() -> None:
    raw = _raw("flashscore_botola_sample_match.json")
    raw["standings"] = {}
    result = StandingsCollector().collect(raw, MatchRef())
    assert result.status == "missing"
    assert result.confidence == 0.0
