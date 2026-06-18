"""Tests for discovery hint merge into match-detail raw payloads."""

from __future__ import annotations

from football_agent.collectors.flashscore.discovery_hints import merge_discovery_hints
from football_agent.collectors.flashscore.fixture_collector import FixtureMatchCollector
from football_agent.collectors.contracts import MatchRef


def test_merge_discovery_hints_replaces_junk_competition_name() -> None:
    raw = {
        "home_team_name": "Levadia",
        "away_team_name": "Kalju",
        "competition_name": "Follow football news coverage from many football leagues",
    }
    hints = {
        "competition_name": "Meistriliiga",
        "competition_country": "Estonia",
        "fixture_date": "2026-06-20",
    }
    merged = merge_discovery_hints(raw, hints)
    assert merged["competition_name"] == "Meistriliiga"
    assert merged["kickoff_utc"] == "2026-06-20T12:00:00+00:00"

    collector = FixtureMatchCollector()
    result = collector.collect(merged, MatchRef(match_url="https://example.com/match"))
    assert result.status == "ok"
