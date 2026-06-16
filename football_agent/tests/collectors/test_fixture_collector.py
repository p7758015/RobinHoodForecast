"""FixtureMatchCollector tests."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.flashscore.fixture_collector import FixtureMatchCollector
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _raw(name: str) -> dict:
    data = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_valid_match_meta() -> None:
    raw = _raw("flashscore_botola_sample_match.json")
    result = FixtureMatchCollector().collect(raw, MatchRef())
    assert result.status == "ok"
    assert result.confidence >= 0.7
    assert result.payload["home_team"] == "Kawkab Marrakech"
    assert result.payload["competition_name"] == "Botola Pro"


def test_unknown_teams_invalid() -> None:
    raw = _raw("flashscore_botola_sample_match.json")
    raw["home_team_name"] = "Unknown"
    raw["away_team_name"] = "Unknown"
    result = FixtureMatchCollector().collect(raw, MatchRef())
    assert result.status == "failed"
    assert result.confidence == 0.0
    assert "match_meta_invalid_teams" in result.warnings


def test_junk_competition_name_invalid() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["competition_name"] = "Latest football news and transfers from around the world today"
    result = FixtureMatchCollector().collect(raw, MatchRef())
    assert result.status == "failed"
    assert any("competition_name" in w for w in result.warnings)
