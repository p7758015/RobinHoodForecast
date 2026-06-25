"""Tests for discovery-aligned settlement and wave diagnostics."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from football_agent.eval_pool.calibration_report import collect_settled_pool_eval_records
from football_agent.eval_pool.settle import classify_result_resolution, is_finished_status, settle_league_pool_with_discovery
from football_agent.eval_pool.wave_diagnostics import build_wave_settlement_diagnostics
from football_agent.eval_pool.wave_manifest import load_wave_manifest
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2

FIXTURES = Path(__file__).parent / "data"


def test_finished_status_recognition_variants() -> None:
    assert is_finished_status("FT") is True
    assert is_finished_status("Finished") is True
    assert is_finished_status("After Pen.") is True
    assert is_finished_status("scheduled") is False
    assert is_finished_status("Postponed") is False


def test_classify_result_resolution() -> None:
    assert classify_result_resolution({"status": "FT", "home_score": 2, "away_score": 1}) == "finished_with_score"
    assert classify_result_resolution({"status": "scheduled"}) == "not_finished"
    assert classify_result_resolution({"status": "FT"}) == "finished_missing_score"


def test_settle_discovery_saves_finished_fixture_with_display_time() -> None:
    raw_finished = {
        "match_id": "kz-1",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "Kaisar",
        "away_team_name": "Aktobe",
        "time_raw": "20.06. 16:00",
        "status": "finished",
        "home_score": 1,
        "away_score": 2,
        "date": "2026-06-20",
        "_discovery_source": True,
        "_discovery_results_source": True,
        "_discovery_date_from": "2026-06-18",
        "_discovery_reference_year": 2026,
        "source_url": "https://www.flashscore.com/match/football/x/x/?mid=kz-1",
    }

    def _results_fetch(entry, date_from, date_to, use_discovery_fallback=True):
        if entry.key == "kazakhstan_premier":
            return [raw_finished]
        return []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "settle.db"
        summary = settle_league_pool_with_discovery(
            date_from="2026-06-18",
            date_to="2026-06-21",
            league_keys=["kazakhstan_premier"],
            db_path=db_path,
            fetch_results_for_entry_fn=_results_fetch,
            enrich_match_detail=False,
        )
        assert summary["fixtures_in_scope"] == 1
        assert summary["results_saved"] == 1
        assert summary["result_source_diagnostics"]["results_endpoint_rows_returned"] == 1
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT match_date, home_score, away_score FROM match_results WHERE home_team='Kaisar'"
        ).fetchone()
        conn.close()
        assert row[0] == "2026-06-20"
        assert row[1:] == (1, 2)


def test_diagnostics_reports_blocker_when_no_match_results(tmp_path: Path) -> None:
    db_path = tmp_path / "diag.db"
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [{"flashscore_stem": "flashscore_kazakhstan_premier_match", "home_score": 2, "away_score": 1}],
        db_path=db_path,
        save_match_results=False,
    )
    manifest = load_wave_manifest(preset="june18_21_first_batch")
    diag = build_wave_settlement_diagnostics(
        manifest,
        db_path=db_path,
        probe_fetch=False,
    )
    assert diag["summary"]["match_results_rows_in_wave_dates"] == 0
    blocker = diag["blocker_analysis"]
    assert blocker["db_has_match_results"] is False
    assert blocker.get("primary_blocker") in (
        "results_fetch_no_in_range_fixtures",
        "results_fetch_not_finished",
        "results_not_persisted",
        "unknown_blocker",
    )


def test_join_stats_counted_in_calibration_collect(tmp_path: Path) -> None:
    db_path = tmp_path / "join.db"
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [{"flashscore_stem": "flashscore_kazakhstan_premier_match", "home_score": 2, "away_score": 1}],
        db_path=db_path,
        save_match_results=True,
    )
    repo = EvaluationRepositoryV2(db_path=db_path)
    rows = list(repo.iter_scored_runs(limit=10))
    records, stats = collect_settled_pool_eval_records(
        rows,
        allowed_keys=("kazakhstan_premier",),
        repo=repo,
    )
    repo.close()
    assert len(records) == 1
    assert stats.get("join_exact_count", 0) >= 1
