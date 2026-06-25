"""Tests for Flashscore HTTP raw enrichment and signal-based block detection."""

from __future__ import annotations

from football_agent.flashscore.raw_enrich import (
    assess_block_signals,
    enrich_http_flashscore_raw,
    form_has_signal,
    standings_has_signal,
)
from football_agent.flashscore.service import FlashscoreIngestionService


def test_enrich_maps_home_away_form_lists() -> None:
    raw = enrich_http_flashscore_raw(
        {
            "match_id": "x1",
            "home_team_name": "A",
            "away_team_name": "B",
            "home_form": ["W", "D", "L"],
            "away_form": ["L", "W"],
        },
    )
    assert form_has_signal(raw["form"])
    assert raw["form"]["home"]["last_n_results"] == ["W", "D", "L"]
    assert "form_mapped_from_home_away_form_lists" in raw["enrichment_warnings"]


def test_enrich_aggregates_h2h_list() -> None:
    raw = enrich_http_flashscore_raw(
        {
            "match_id": "x2",
            "h2h": [{"score": "2-1"}, {"score": "1-1"}, {"score": "0-3"}],
        },
    )
    assert raw["h2h"]["recent_h2h_matches"] == 3
    assert raw["h2h"]["home_h2h_wins"] == 1
    assert raw["h2h"]["h2h_draws"] == 1
    assert raw["h2h"]["away_h2h_wins"] == 1


def test_enrich_maps_flat_standings() -> None:
    raw = enrich_http_flashscore_raw(
        {"home_position": 3, "away_position": 7, "home_points": 21},
    )
    assert standings_has_signal(raw["standings"])
    assert raw["standings"]["home_position"] == 3


def test_enrich_maps_schedule_flat_fields() -> None:
    from football_agent.flashscore.raw_enrich import schedule_has_signal

    raw = enrich_http_flashscore_raw(
        {
            "previous_match_date_home": "2026-06-10",
            "recent_match_dates_home": ["2026-06-10", "2026-06-05"],
        },
    )
    assert schedule_has_signal(raw["schedule_raw"])
    assert raw["schedule_raw"]["previous_match_date_home"] == "2026-06-10"


def test_enrich_backfills_standings_points_from_flat() -> None:
    raw = enrich_http_flashscore_raw(
        {
            "standings": {"home_position": 2, "away_position": 9},
            "home_points": 18,
            "home_goal_difference": 4,
        },
    )
    assert raw["standings"]["home_points"] == 18
    assert raw["standings"]["home_goal_difference"] == 4


def test_stub_zeros_not_counted_as_signal() -> None:
    signals = assess_block_signals(
        {
            "standings": {"home_position": 0, "away_position": 0, "home_points": 0},
            "form": {
                "home": {"last_n_results": [], "last_n_points": 0},
                "away": {"last_n_results": [], "last_n_points": 0},
            },
            "h2h": {"recent_h2h_matches": 0, "home_h2h_wins": 0},
        },
    )
    assert signals["standings"] is False
    assert signals["form"] is False
    assert signals["h2h"] is False


def test_ingestion_drops_empty_form_stub() -> None:
    svc = FlashscoreIngestionService.__new__(FlashscoreIngestionService)
    facts = svc._map_raw_to_facts(  # type: ignore[attr-defined]
        {
            "match_id": "min",
            "home_team_name": "H",
            "away_team_name": "A",
            "form": {
                "home": {"last_n_results": [], "last_n_points": 0},
                "away": {"last_n_results": [], "last_n_points": 0},
            },
        },
    )
    assert facts.form is None
    assert "form" in facts.provenance.missing_blocks
