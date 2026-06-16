"""
Evaluation groundwork ingest models (Phase Evaluation A).

Lightweight records joining prediction, odds coverage, and optional settlement.
Designed to feed future full offline evaluation runners without re-scoring.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from football_agent.domain.models_v2 import MatchPredictionResultV2, V2IngestModel
from football_agent.odds.coverage import build_match_odds_coverage
from football_agent.odds.coverage_models import MatchOddsCoverage
from football_agent.odds.models import MatchOddsContext


class ActualResultSnapshot(V2IngestModel):
    """Settled match result (no future leakage when read from persistence)."""

    match_date: str
    home_score: int
    away_score: int
    settled_at_utc: Optional[datetime] = None
    join_method: Optional[str] = None


class PredictionSnapshot(V2IngestModel):
    """Minimal prediction payload for evaluation ingest."""

    best_market_key: Optional[str] = None
    best_market_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    best_market_book_odds: Optional[float] = Field(default=None, gt=1.0)
    market_predictions: List[Dict[str, Any]] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    express_allow: bool = False


class EvaluationGroundworkRecord(V2IngestModel):
    """
    One evaluable unit: prediction + odds coverage + optional actual result.

    Maps conceptually to analysis_runs_v2 / analysis_predictions_v2 / snapshots
    without requiring full ORM writes on this phase.
    """

    match_key: str
    match_id: Optional[str] = None
    match_date: Optional[str] = None
    home_team: str
    away_team: str
    competition_code: Optional[str] = None
    competition_name: Optional[str] = None

    prediction: PredictionSnapshot
    odds_coverage: MatchOddsCoverage
    has_odds: bool = False
    has_prediction: bool = True
    prediction_only: bool = False

    actual_result: Optional[ActualResultSnapshot] = None
    run_id: Optional[str] = None
    ingested_at_utc: datetime


def _prediction_probs_from_result(result: MatchPredictionResultV2) -> Dict[str, float]:
    from football_agent.evaluation.market_key_map import scorer_market_to_coverage_key

    out: Dict[str, float] = {}
    for mp in result.market_predictions:
        cov_key = scorer_market_to_coverage_key(mp.market_key)
        if cov_key:
            out[cov_key] = float(mp.probability)
    return out


def prediction_snapshot_from_result(result: MatchPredictionResultV2) -> PredictionSnapshot:
    best = result.best_market
    markets = [
        {
            "market_key": mp.market_key,
            "probability": mp.probability,
            "book_odds": mp.book_odds,
            "fair_odds": mp.fair_odds,
            "edge": mp.edge,
        }
        for mp in result.market_predictions
    ]
    return PredictionSnapshot(
        best_market_key=best.market_key if best else None,
        best_market_probability=best.probability if best else None,
        best_market_book_odds=best.book_odds if best else None,
        market_predictions=markets,
        overall_confidence=result.overall_confidence_score,
        express_allow=bool(result.express_safety.allow_for_express),
    )


def build_evaluation_record(
    *,
    match_key: str,
    home_team: str,
    away_team: str,
    prediction: Optional[MatchPredictionResultV2] = None,
    prediction_snapshot: Optional[PredictionSnapshot] = None,
    odds_context: Optional[MatchOddsContext] = None,
    actual_result: Optional[ActualResultSnapshot] = None,
    match_id: Optional[str] = None,
    match_date: Optional[str] = None,
    competition_code: Optional[str] = None,
    competition_name: Optional[str] = None,
    run_id: Optional[str] = None,
    ingested_at_utc: Optional[datetime] = None,
) -> EvaluationGroundworkRecord:
    """Assemble one groundwork record from scorer output + odds context."""
    from datetime import timezone

    now = ingested_at_utc or datetime.now(timezone.utc)

    if prediction_snapshot is None and prediction is not None:
        prediction_snapshot = prediction_snapshot_from_result(prediction)
    if prediction_snapshot is None:
        prediction_snapshot = PredictionSnapshot()

    predicted_probs = (
        _prediction_probs_from_result(prediction) if prediction is not None else None
    )
    coverage = build_match_odds_coverage(odds_context, predicted_probabilities=predicted_probs)

    has_odds = coverage.has_any_odds or (
        prediction_snapshot.best_market_book_odds is not None
        and prediction_snapshot.best_market_book_odds > 1.0
    )
    prediction_only = not has_odds and bool(prediction_snapshot.best_market_probability)

    return EvaluationGroundworkRecord(
        match_key=match_key,
        match_id=match_id,
        match_date=match_date,
        home_team=home_team,
        away_team=away_team,
        competition_code=competition_code,
        competition_name=competition_name,
        prediction=prediction_snapshot,
        odds_coverage=coverage,
        has_odds=has_odds,
        has_prediction=True,
        prediction_only=prediction_only,
        actual_result=actual_result,
        run_id=run_id,
        ingested_at_utc=now,
    )
