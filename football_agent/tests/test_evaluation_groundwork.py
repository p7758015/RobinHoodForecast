"""Phase Evaluation A — metrics and groundwork ingest tests."""

from __future__ import annotations

from datetime import datetime, timezone

from football_agent.evaluation.groundwork_models import (
    ActualResultSnapshot,
    EvaluationGroundworkRecord,
    PredictionSnapshot,
    build_evaluation_record,
)
from football_agent.evaluation.metrics import (
    build_calibration_groundwork,
    compute_odds_coverage_metrics,
    compute_best_market_hit_summary,
    summarize_groundwork_records,
)
from football_agent.odds.coverage import build_match_odds_coverage
from football_agent.tests.test_odds_coverage import _full_odds_context


def _record(
    *,
    has_odds: bool,
    best_key: str = "HOME_WIN",
    prob: float = 0.75,
    book: float | None = 1.9,
    actual: ActualResultSnapshot | None = None,
) -> EvaluationGroundworkRecord:
    ctx = _full_odds_context() if has_odds else None
    cov = build_match_odds_coverage(ctx)
    return EvaluationGroundworkRecord(
        match_key="k1",
        home_team="A",
        away_team="B",
        prediction=PredictionSnapshot(
            best_market_key=best_key,
            best_market_probability=prob,
            best_market_book_odds=book,
        ),
        odds_coverage=cov,
        has_odds=has_odds,
        prediction_only=not has_odds,
        actual_result=actual,
        ingested_at_utc=datetime.now(timezone.utc),
    )


def test_coverage_metrics_mixed_sample() -> None:
    coverages = [
        build_match_odds_coverage(_full_odds_context()),
        build_match_odds_coverage(None),
    ]
    metrics = compute_odds_coverage_metrics(coverages)
    assert metrics["sample_size"] == 2
    assert metrics["any_odds_rate"] == 0.5
    assert metrics["by_group"]["1x2"] == 0.5
    assert metrics["by_market"]["home_win"] == 0.5


def test_best_market_hit_summary() -> None:
    actual = ActualResultSnapshot(match_date="2026-06-11", home_score=2, away_score=1)
    records = [
        _record(has_odds=True, best_key="HOME_WIN", prob=0.8, actual=actual),
        _record(has_odds=True, best_key="AWAY_WIN", prob=0.7, actual=actual),
    ]
    summary = compute_best_market_hit_summary(records)
    assert summary["settled_count"] == 2
    assert summary["hit_count"] == 1
    assert summary["hit_rate"] == 0.5


def test_calibration_groundwork_buckets() -> None:
    samples = [(0.55, True), (0.55, False), (0.75, True), (0.85, True)]
    report = build_calibration_groundwork(samples)
    assert report["sample_size"] == 4
    buckets = report["buckets"]
    assert any(b["count"] > 0 for b in buckets)


def test_build_evaluation_record_prediction_only() -> None:
    rec = build_evaluation_record(
        match_key="x",
        home_team="H",
        away_team="A",
        odds_context=None,
        prediction_snapshot=PredictionSnapshot(
            best_market_key="HOME_WIN",
            best_market_probability=0.8,
            best_market_book_odds=None,
        ),
    )
    assert rec.prediction_only is True
    assert rec.has_odds is False


def test_summarize_groundwork_records() -> None:
    records = [_record(has_odds=True), _record(has_odds=False)]
    report = summarize_groundwork_records(records)
    assert report["sample_size"] == 2
    assert report["prediction_only_rate"] == 0.5
    assert "odds_coverage" in report
    assert "calibration" in report
