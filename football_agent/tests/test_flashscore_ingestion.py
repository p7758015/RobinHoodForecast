"""Tests for Flashscore normalized facts ingestion and debug summary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from football_agent.debug.flashscore_trace import build_facts_summary
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreSeasonContextInputs
from football_agent.flashscore.service import FlashscoreIngestionService


FIXTURES_DIR = Path(__file__).parent / "data"


def _service() -> FlashscoreIngestionService:
    return FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))


def test_raw_fixture_maps_to_match_facts() -> None:
    svc = _service()
    facts = svc.get_facts_for_match("flashscore_sample_league_match")
    assert isinstance(facts, FlashscoreMatchFacts)
    assert facts.meta.competition_name == "Serie A"
    assert facts.meta.home_team_name == "AC Milan"
    assert facts.standings is not None
    assert facts.standings.home_position == 3
    assert facts.standings.away_points == 24


def test_missing_optional_blocks_do_not_crash() -> None:
    # create a minimal raw record without optional blocks
    adapter = FixtureFileFlashscoreAdapter(FIXTURES_DIR)
    svc = FlashscoreIngestionService(adapter)
    raw = {
        "match_id": "fs-minimal",
        "source_url": "https://example.com/match/fs-minimal",
        "competition_name": "Test League",
        "home_team_name": "Home",
        "away_team_name": "Away",
        "status": "SCHEDULED",
    }
    facts = svc._map_raw_to_facts(raw)  # type: ignore[attr-defined]
    assert facts.meta.match_id == "fs-minimal"
    assert facts.standings is None
    assert "standings" in facts.provenance.missing_blocks


def test_season_context_inputs_preserved() -> None:
    svc = _service()
    facts = svc.get_facts_for_match("flashscore_sample_league_match")
    sci = facts.season_context_inputs
    assert isinstance(sci, FlashscoreSeasonContextInputs)
    assert sci.matchday_number == 14
    assert sci.total_matchdays == 38
    assert sci.rounds_remaining_after_this_match == 24
    assert sci.table_neighbors["ucl_cutoff_pos"] == 4


def test_debug_summary_from_facts() -> None:
    svc = _service()
    facts = svc.get_facts_for_match("flashscore_sample_league_match")
    summary = build_facts_summary(facts)
    assert summary["meta"]["competition_name"] == "Serie A"
    assert summary["blocks"]["standings"] == "yes"
    assert summary["blocks"]["form"] == "yes"
    assert "provenance" in summary
    derived = summary.get("derived_season_motivation") or {}
    assert derived.get("gap_to_title_points") is not None

