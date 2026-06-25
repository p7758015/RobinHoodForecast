"""
Scoring integration layer (additive-only):

MatchAnalysisSnapshotV2 (+ BuildReport sidecar) -> routing -> LeagueScorerV2 | parked.

Constraints:
- Does NOT mutate or "clean up" the snapshot
- Does NOT perform ingestion/merge/builder work
- LeagueScorerV2 formulas unchanged — only gated by tournament routing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, MatchPredictionResultV2
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.scorers.parked_prediction import build_parked_prediction
from football_agent.scorers.routing import ScorerRoutingDecision, resolve_scorer_route
from football_agent.services.competition_classifier import CompetitionClassification


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ScoredRunV2:
    """Result wrapper for snapshot scoring with sidecar provenance."""

    snapshot: MatchAnalysisSnapshotV2
    prediction: MatchPredictionResultV2
    build_report: BuildReport
    routing_decision: Optional[ScorerRoutingDecision] = None
    scoring_skipped: bool = False

    scoring_warnings: List[str] = field(default_factory=list)
    scored_at_utc: datetime = field(default_factory=_utc_now)
    scorer_name: str = "LeagueScorerV2"
    scorer_version: Optional[str] = None


class ScoringServiceV2:
    """
    Public API:
    - score_snapshot_with_report(snapshot, report) -> ScoredRunV2   (primary)
    - score_snapshot(snapshot) -> MatchPredictionResultV2          (convenience wrapper)
    """

    def __init__(self, scorer: Optional[LeagueScorerV2] = None) -> None:
        self._scorer = scorer or LeagueScorerV2()

    def score_snapshot_with_report(
        self,
        snapshot: MatchAnalysisSnapshotV2,
        report: BuildReport,
        *,
        classification: CompetitionClassification | None = None,
        include_express_reasons_as_warnings: bool = True,
    ) -> ScoredRunV2:
        decision = resolve_scorer_route(classification, snapshot=snapshot)

        if decision.route == "league_full":
            prediction = self._scorer.score_snapshot(snapshot)
            from football_agent.scorers.selection_policy import apply_calibration_selection_policy

            prediction = apply_calibration_selection_policy(prediction, snapshot)
            scorer_name = "LeagueScorerV2"
            scoring_skipped = False
        else:
            prediction = build_parked_prediction(snapshot, decision)
            scorer_name = "routing_parked"
            scoring_skipped = True

        warnings: List[str] = [decision.reason]
        if (
            not scoring_skipped
            and include_express_reasons_as_warnings
            and prediction.express_safety
            and prediction.express_safety.reasons
        ):
            warnings.extend(
                r for r in prediction.express_safety.reasons if r != decision.reason
            )

        return ScoredRunV2(
            snapshot=snapshot,
            prediction=prediction,
            build_report=report,
            routing_decision=decision,
            scoring_skipped=scoring_skipped,
            scoring_warnings=warnings,
            scorer_name=scorer_name,
        )

    def score_snapshot(
        self,
        snapshot: MatchAnalysisSnapshotV2,
        *,
        classification: CompetitionClassification | None = None,
    ) -> MatchPredictionResultV2:
        decision = resolve_scorer_route(classification, snapshot=snapshot)
        if decision.route == "league_full":
            prediction = self._scorer.score_snapshot(snapshot)
            from football_agent.scorers.selection_policy import apply_calibration_selection_policy

            prediction = apply_calibration_selection_policy(prediction, snapshot)
            return prediction
        return build_parked_prediction(snapshot, decision)
