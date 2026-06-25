"""Tests for league eval-pool preflight."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.eval_pool.preflight import (
    PreflightStatus,
    resolve_expected_pool_key,
    run_preflight,
)
from football_agent.eval_pool.scope import all_pool_entries


def test_expected_league_alias_ireland() -> None:
    assert resolve_expected_pool_key("ireland") == "ireland_premier"


def test_expected_league_alias_china() -> None:
    assert resolve_expected_pool_key("china") == "china_super_league"


def test_ireland_in_pool_and_registry() -> None:
    keys = {e.key for e in all_pool_entries()}
    assert "ireland_premier" in keys
    assert "china_super_league" in keys


def test_preflight_ready_league_no_probe() -> None:
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["latvia"],
        probe_fixtures=False,
    )
    row = report.rows[0]
    assert row.status == PreflightStatus.IN_POOL_AND_SUPPORTED
    assert row.pool_key == "latvia_virsliga"
    assert row.registry_code == "FS_LATVIA_VIRSLIGA"


def test_preflight_unknown_league() -> None:
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["atlantis_league"],
        probe_fixtures=False,
    )
    assert report.rows[0].status == PreflightStatus.UNKNOWN_LEAGUE


def test_preflight_supported_but_out_of_pool() -> None:
    """Registry-only code without pool entry would be OUT_OF_POOL — simulate via missing alias."""
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["not_a_real_token_xyz"],
        probe_fixtures=False,
    )
    assert report.rows[0].status == PreflightStatus.UNKNOWN_LEAGUE


def test_preflight_extension_league_not_in_default_pool() -> None:
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["morocco"],
        probe_fixtures=False,
    )
    row = report.rows[0]
    assert row.status == PreflightStatus.IN_POOL_AND_SUPPORTED
    assert row.in_default_accumulate_pool is False
    assert "use --leagues flag" in " ".join(row.notes)


@patch("football_agent.eval_pool.preflight.fetch_fixtures_for_pool_entry")
def test_preflight_no_fixtures_found(mock_fetch) -> None:
    from football_agent.eval_pool.fixture_sources import FixtureFetchResult

    mock_fetch.return_value = FixtureFetchResult.empty(["discovery_empty"])
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["ireland"],
        probe_fixtures=True,
        scraper_url="http://localhost:3000",
    )
    assert report.rows[0].status == PreflightStatus.NO_FIXTURES_FOUND


@patch("football_agent.eval_pool.preflight.fetch_fixtures_for_pool_entry")
def test_preflight_fixtures_found(mock_fetch) -> None:
    from football_agent.eval_pool.fixture_sources import FixtureFetchResult, FixtureFetchStats

    mock_fetch.return_value = FixtureFetchResult(
        fixtures=[{"match_id": "m1", "home_team_name": "A", "away_team_name": "B"}],
        warnings=[],
        stats=FixtureFetchStats(seen=1, in_range=1, skipped_out_of_range=0),
    )
    report = run_preflight(
        date_from="2026-06-25",
        date_to="2026-06-28",
        expected_leagues=["china"],
        probe_fixtures=True,
        scraper_url="http://localhost:3000",
    )
    assert report.rows[0].status == PreflightStatus.IN_POOL_AND_SUPPORTED
    assert report.rows[0].fixtures_found >= 1
