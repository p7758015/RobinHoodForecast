"""Tests for league eval-pool calibration report layer."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from football_agent.eval_pool.calibration_report import (
    MIN_SETTLED_FOR_DIAGNOSTICS,
    build_pool_calibration_review,
    collect_settled_pool_eval_records,
    extract_risk_tags,
    pool_confidence_bucket_label,
)
from football_agent.eval_pool.report import LeagueEvalPoolReporter
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2

FIXTURES = Path(__file__).parent / "data"


def test_confidence_bucket_labels() -> None:
    assert pool_confidence_bucket_label(0.55) == "0.50-0.59"
    assert pool_confidence_bucket_label(0.65) == "0.60-0.69"
    assert pool_confidence_bucket_label(0.75) == "0.70-0.79"
    assert pool_confidence_bucket_label(0.85) == "0.80+"
    assert pool_confidence_bucket_label(0.40) is None


def test_extract_risk_flags_from_prediction() -> None:
    pred = {
        "home_scoring": {
            "summary_flags": ["new_coach", "pre_big_match_risk"],
            "factor_scores": {"squad_availability": 0.35, "schedule_context": 0.55},
        },
        "away_scoring": {
            "summary_flags": ["thin_squad"],
            "factor_scores": {"squad_availability": 0.60, "schedule_context": 0.40},
        },
        "best_market": {"market_key": "HOME_WIN", "probability": 0.62},
    }
    tags = extract_risk_tags(prediction=pred, snapshot_json={}, report_json={"odds_link_strategy": "partial"})
    assert "new_coach" in tags
    assert "pre_big_match_risk" in tags
    assert "thin_squad" in tags
    assert "low_squad_confidence" in tags
    assert "low_schedule_confidence" in tags
    assert "no_best_market_odds" in tags
    assert "partial_odds_link" in tags


def _persist_kz_with_confidence(db_path: Path, *, confidence: float, market_key: str, prob: float, book_odds: float) -> None:
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [
            {
                "flashscore_stem": "flashscore_kazakhstan_premier_match",
                "home_score": 2,
                "away_score": 1,
            }
        ],
        db_path=db_path,
        save_match_results=True,
    )
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT prediction_json FROM analysis_predictions_v2 LIMIT 1").fetchone()
    pred = json.loads(row[0])
    pred["overall_confidence_score"] = confidence
    pred["best_market"] = {
        "market_key": market_key,
        "probability": prob,
        "book_odds": book_odds,
        "label": market_key,
    }
    conn.execute(
        "UPDATE analysis_predictions_v2 SET prediction_json=?",
        (json.dumps(pred, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()


def test_calibration_buckets_on_settled_fixture(tmp_path: Path) -> None:
    db_path = tmp_path / "cal.db"
    _persist_kz_with_confidence(db_path, confidence=0.72, market_key="HOME_WIN", prob=0.68, book_odds=1.9)

    reporter = LeagueEvalPoolReporter(db_path=db_path)
    try:
        review = reporter.build_calibration_review(league_keys=["kazakhstan_premier"])
    finally:
        reporter.close()

    assert review["sample"]["settled_evaluable_runs"] == 1
    conf_buckets = {b["confidence_bucket"]: b for b in review["confidence_buckets"]}
    assert "0.70-0.79" in conf_buckets
    assert conf_buckets["0.70-0.79"]["count"] == 1
    assert conf_buckets["0.70-0.79"]["hit_rate"] == 1.0

    market_buckets = {b["market_key"]: b for b in review["market_buckets"]}
    assert market_buckets["HOME_WIN"]["count"] == 1

    league_buckets = {b["pool_key"]: b for b in review["league_buckets"]}
    assert league_buckets["kazakhstan_premier"]["count"] == 1
    assert "brazil_serie_b" not in league_buckets


def test_parked_excluded_from_calibration(tmp_path: Path) -> None:
    db_path = tmp_path / "parked_cal.db"
    _persist_kz_with_confidence(db_path, confidence=0.70, market_key="HOME_WIN", prob=0.65, book_odds=1.8)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE analysis_predictions_v2
           SET prediction_json = json_set(prediction_json, '$.analysis_mode', 'analysis_only')
        """
    )
    conn.commit()
    conn.close()

    repo = EvaluationRepositoryV2(db_path=db_path)
    try:
        rows = list(repo.iter_scored_runs(limit=10))
        records, stats = collect_settled_pool_eval_records(
            rows,
            allowed_keys=("kazakhstan_premier",),
            repo=repo,
        )
    finally:
        repo.close()

    assert stats["parked_skipped"] == 1
    assert len(records) == 0


def test_league_buckets_do_not_mix_scopes(tmp_path: Path) -> None:
    db_path = tmp_path / "multi.db"
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [
            {
                "flashscore_stem": "flashscore_kazakhstan_premier_match",
                "home_score": 1,
                "away_score": 0,
            }
        ],
        db_path=db_path,
        save_match_results=True,
    )

    reporter = LeagueEvalPoolReporter(db_path=db_path)
    try:
        kz = reporter.build_calibration_review(league_keys=["kazakhstan_premier"])
        br = reporter.build_calibration_review(league_keys=["brazil_serie_b"])
    finally:
        reporter.close()

    assert kz["sample"]["settled_evaluable_runs"] == 1
    assert br["sample"]["settled_evaluable_runs"] == 0
    assert kz["league_buckets"][0]["pool_key"] == "kazakhstan_premier"


def test_insufficient_sample_fail_soft() -> None:
    records = []
    review = build_pool_calibration_review(records)
    assert review["diagnostics"]["status"] == "insufficient_sample"
    assert review["sample"]["settled_evaluable_runs"] == 0
    assert MIN_SETTLED_FOR_DIAGNOSTICS >= 8


def test_diagnostics_overconfidence_signal() -> None:
    from football_agent.eval_pool.calibration_report import SettledPoolEvalRecord

    records = [
        SettledPoolEvalRecord(
            run_id=f"r{i}",
            pool_key="kazakhstan_premier",
            competition_name="KZ PL",
            market_key="HOME_NOT_LOSE",
            probability=0.78,
            confidence=0.82,
            book_odds=1.5,
            outcome=False,
            has_snapshot_odds=True,
            risk_tags=("pre_big_match_risk",),
        )
        for i in range(10)
    ]
    review = build_pool_calibration_review(records)
    assert review["diagnostics"]["status"] in ("ok", "no_strong_signals", "insufficient_sample") or review["diagnostics"]["findings"]
    # 10 runs at 0.82 conf, 0% hit — should flag overconfidence
    types = {f["type"] for f in review["diagnostics"].get("findings") or []}
    assert "overconfidence" in types
