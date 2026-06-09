"""Tests for v2 offline market outcomes, reports, and batch persist bridge."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from football_agent.offline.market_outcomes import v2_market_is_win
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_and_evaluate
from football_agent.offline.v2_reports import get_v2_accuracy_report, get_v2_calibration_report
from football_agent.storage.v2_database import V2Database

FIXTURES_DIR = Path(__file__).parent / "data"


def test_v2_market_outcomes() -> None:
    assert v2_market_is_win("HOME_WIN", 2, 1) is True
    assert v2_market_is_win("AWAY_WIN", 2, 1) is False
    assert v2_market_is_win("HOME_NOT_LOSE", 1, 1) is True
    assert v2_market_is_win("BTTS_YES", 1, 0) is False
    assert v2_market_is_win("HOME_TEAM_TO_SCORE", 1, 0) is True
    assert v2_market_is_win("AWAY_TEAM_TO_SCORE", 0, 2) is True
    assert v2_market_is_win("OVER_1_5", 1, 1) is True
    assert v2_market_is_win("OVER_1_5", 1, 0) is False


def test_v2_reports_on_fixture_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = V2Database(db_path)
        db.save_match_result("2024-04-25", "Home FC", "Away FC", 2, 1)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO v2_predictions
            (match_date, competition, home_team, away_team, match_id, market_key,
             probability, book_odds, fair_odds, edge, pipeline_version,
             season_progress, h2h_btts_rate, express_safety_class, overall_confidence, created_at)
            VALUES ('2024-04-25','PL','Home FC','Away FC',1,'HOME_WIN',0.72,1.55,1.39,0.08,'v2',
                    0.8,0.55,'EXPRESS_SAFE',0.7,'2024-04-25T12:00:00')
            """
        )
        conn.commit()
        conn.close()

        try:
            acc = get_v2_accuracy_report(db)
            assert acc["overall"]["total"] == 1
            assert acc["overall"]["wins"] == 1
            cal = get_v2_calibration_report(db)
            assert cal["probability_buckets"]
        finally:
            db.close()


def test_v2_batch_persist_fixture_bridge_evaluation(tmp_path: Path) -> None:
    db_path = tmp_path / "batch_bridge.db"
    item = {
        "flashscore_stem": "flashscore_sample_league_match",
        "openclaw_stem": "openclaw_context_sample",
        "odds_stem": "odds_sample",
        "home_score": 1,
        "away_score": 1,
    }
    out = run_v2_batch_persist_and_evaluate(
        FIXTURES_DIR,
        [item],
        db_path=db_path,
        save_match_results=True,
    )

    batch = out["batch"]
    assert batch["runs_persisted"] == 1
    assert batch["match_results_saved"] == 1
    assert batch["runs"][0]["run_status"] == "scored"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_status FROM analysis_runs_v2 WHERE run_id=?",
            (batch["runs"][0]["run_id"],),
        ).fetchone()
        assert row is not None
        assert row[0] == "scored"
    finally:
        conn.close()

    evaluation = out["evaluation"]
    counts = evaluation["counts"]
    metrics = evaluation["metrics"]
    assert counts["scored_runs_total"] >= 1
    assert counts["evaluable_runs_total"] >= 1
    assert counts["settled_runs_total"] >= 1
    assert metrics["settled_coverage"] > 0.0
    assert counts["join_exact_count"] >= 1
