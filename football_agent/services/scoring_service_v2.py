"""
Scoring integration layer (additive-only):

MatchAnalysisSnapshotV2 (+ BuildReport sidecar) -> LeagueScorerV2 -> ScoredRunV2.

Constraints:
- Does NOT mutate or "clean up" the snapshot
- Does NOT perform ingestion/merge/builder work
- Does NOT introduce new warning semantics or scoring logic
- Warnings are either empty or a transparent aggregation of existing scorer-side reasons
  (e.g., `prediction.express_safety.reasons`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, MatchPredictionResultV2
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ScoredRunV2:
    """Result wrapper for snapshot scoring with sidecar provenance."""

    snapshot: MatchAnalysisSnapshotV2
    prediction: MatchPredictionResultV2
    build_report: BuildReport

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
        include_express_reasons_as_warnings: bool = True,
    ) -> ScoredRunV2:
        # Critical: do not mutate snapshot; pass through as-is.
        prediction = self._scorer.score_snapshot(snapshot)

        warnings: List[str] = []
        if include_express_reasons_as_warnings and prediction.express_safety and prediction.express_safety.reasons:
            warnings = list(prediction.express_safety.reasons)

        return ScoredRunV2(
            snapshot=snapshot,
            prediction=prediction,
            build_report=report,
            scoring_warnings=warnings,
        )

    def score_snapshot(self, snapshot: MatchAnalysisSnapshotV2) -> MatchPredictionResultV2:
        # Thin wrapper for legacy/simple use cases.
        return self._scorer.score_snapshot(snapshot)

