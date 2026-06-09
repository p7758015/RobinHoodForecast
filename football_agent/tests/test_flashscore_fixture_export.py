"""Tests for Flashscore fixture export (debug/live ingestion helper)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from football_agent.debug import flashscore_trace
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.fixture_export import (
    coerce_raw_to_fixture_record,
    default_fixture_stem,
    extract_match_id_from_url,
    write_fixture_json,
)
from football_agent.flashscore.service import FlashscoreIngestionService


def test_extract_match_id_from_flashscore_url() -> None:
    url = "https://www.flashscore.com/match/football/kawkab/raja/?mid=dC2J6FlK"
    assert extract_match_id_from_url(url) == "dC2J6FlK"


def test_coerce_http_shape_to_fixture_record() -> None:
    raw = {
        "match_id": "dhCPpYR0",
        "url": "https://www.flashscore.com/match/football/cod/yacoub/?mid=dhCPpYR0",
        "home_team": "COD Meknes",
        "away_team": "Yacoub El Mansour",
        "competition": "Botola Pro",
        "competition_country": "Morocco",
        "kickoff_utc": "2026-06-09T19:00:00+00:00",
        "status": "scheduled",
        "scraper_backend_name": "http",
    }
    out = coerce_raw_to_fixture_record(raw)
    assert out["match_id"] == "dhCPpYR0"
    assert out["home_team_name"] == "COD Meknes"
    assert out["away_team_name"] == "Yacoub El Mansour"
    assert out["competition_name"] == "Botola Pro"
    assert out["kickoff_utc"] == "2026-06-09T19:00:00+00:00"
    assert out["status"] == "scheduled"
    assert isinstance(out["standings"], dict)
    assert isinstance(out["form"]["home"]["last_n_results"], list)
    assert out["h2h"]["recent_h2h_matches"] == 0


def test_coerce_preserves_existing_full_fixture() -> None:
    fixtures = Path(__file__).parent / "data"
    raw = FixtureFileFlashscoreAdapter(fixtures).fetch_match_raw("flashscore_botola_sample_match")
    out = coerce_raw_to_fixture_record(raw)
    assert out["match_id"] == raw["match_id"]
    assert out["standings"]["home_position"] == raw["standings"]["home_position"]
    assert out["season_context"]["matchday_number"] == raw["season_context"]["matchday_number"]


def test_coerced_fixture_loads_via_ingestion_service(tmp_path: Path) -> None:
    raw = {
        "match_id": "test-export-1",
        "home_team_name": "Home A",
        "away_team_name": "Away B",
        "competition_name": "Test League",
        "kickoff_utc": "2026-06-09T20:00:00+00:00",
    }
    record = coerce_raw_to_fixture_record(raw)
    path = write_fixture_json(tmp_path / "flashscore_test-export-1.json", record)
    facts = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(tmp_path)).get_facts_for_match(
        "flashscore_test-export-1"
    )
    assert path.exists()
    assert facts is not None
    assert facts.meta.home_team_name == "Home A"


def test_default_fixture_stem() -> None:
    assert default_fixture_stem({"match_id": "dC2J6FlK"}) == "flashscore_dC2J6FlK"


def test_flashscore_trace_live_mode_saves_fixture(tmp_path: Path) -> None:
    http_raw = {
        "match_id": "live-99",
        "url": "https://www.flashscore.com/match/football/a/b/?mid=live-99",
        "home_team": "Team H",
        "away_team": "Team A",
        "competition_name": "Botola Pro",
        "kickoff_utc": "2026-06-09T21:00:00+00:00",
    }
    out_dir = tmp_path / "exported"

    with patch.object(flashscore_trace, "_fetch_live_raw", return_value=http_raw):
        code = flashscore_trace.main(
            [
                "--match-url",
                http_raw["url"],
                "--flashscore-url",
                "http://localhost:3000",
                "--output-dir",
                str(out_dir),
                "--json",
            ]
        )

    assert code == 0
    saved = out_dir / "flashscore_live-99.json"
    assert saved.exists()
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["home_team_name"] == "Team H"
    assert payload["match_id"] == "live-99"


def test_flashscore_trace_live_mode_missing_url_exit_2() -> None:
    with patch.object(flashscore_trace.config, "FLASHSCORE_SCRAPER_URL", None):
        code = flashscore_trace.main(["--match-id", "abc"])
    assert code == 2
