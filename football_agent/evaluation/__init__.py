"""Offline evaluation groundwork (Phase Evaluation A)."""

from football_agent.evaluation.groundwork_models import (
    EvaluationGroundworkRecord,
    build_evaluation_record,
)
from football_agent.evaluation.metrics import (
    build_calibration_groundwork,
    compute_best_market_hit_summary,
    compute_odds_coverage_metrics,
)

__all__ = [
    "EvaluationGroundworkRecord",
    "build_evaluation_record",
    "build_calibration_groundwork",
    "compute_best_market_hit_summary",
    "compute_odds_coverage_metrics",
]
