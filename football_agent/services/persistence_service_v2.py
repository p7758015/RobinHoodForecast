"""
Persistence façade for v2 analysis runs.

This service is intentionally side-effectful (writes to SQLite) but is NOT wired
into Telegram/app_pipeline runtime in this step. It is used by debug/offline tooling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from football_agent.analysis_merge.models import MergedMatchAnalysisContext
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.storage.v2_run_repository import AnalysisRunRepositoryV2


class SnapshotPersistenceServiceV2:
    def __init__(self, *, db_path: str | Path | None = None, repo: Optional[AnalysisRunRepositoryV2] = None) -> None:
        self._repo = repo or AnalysisRunRepositoryV2(db_path=db_path)

    def persist_scored_run(
        self,
        *,
        merged: MergedMatchAnalysisContext,
        scored: ScoredRunV2,
    ) -> str:
        return self._repo.persist_scored_run_atomic(
            merged=merged,
            snapshot=scored.snapshot,
            report=scored.build_report,
            prediction=scored.prediction,
            scoring_warnings=list(scored.scoring_warnings),
        )

    def load_run(self, run_id: str):
        return self._repo.load_run(run_id)

    def load_latest_run_for_match_key(self, match_key: str):
        return self._repo.load_latest_run_for_match_key(match_key)

    def close(self) -> None:
        self._repo.close()

