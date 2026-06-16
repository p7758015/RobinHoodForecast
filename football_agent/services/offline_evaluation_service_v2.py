"""
Offline evaluation service over persisted analysis runs.

No future leakage: reads only stored artifacts + final match_results.

Typical Stage 1 offline flow
-----------------------------
1. Batch persist scored runs (fixtures) via
   ``offline.v2_calibration_runner.run_v2_batch_persist_from_fixtures``
   or CLI ``python -m football_agent.offline.v2_calibrate --mode batch-persist ...``
2. Ensure ``match_results`` rows exist for the same identities (batch can save them)
3. ``OfflineEvaluationServiceV2.evaluate(...)`` for settled coverage / join metrics
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from football_agent.offline.evaluation_v2 import (
    SETTLEMENT_IDENTITY_CONTRACT,
    evaluate_best_market_runs,
    extract_settlement_identity,
)
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2


def _competition_name_from_snapshot(snapshot_json: Optional[dict]) -> Optional[str]:
    if not isinstance(snapshot_json, dict):
        return None
    meta = snapshot_json.get("match_meta")
    if not isinstance(meta, dict):
        return None
    name = meta.get("competition_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


class OfflineEvaluationServiceV2:
    def __init__(self, *, db_path: str | Path | None = None, repo: Optional[EvaluationRepositoryV2] = None) -> None:
        self._repo = repo or EvaluationRepositoryV2(db_path=db_path)

    def evaluate(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        match_key: Optional[str] = None,
        competition_code: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        rows = list(
            self._repo.iter_scored_runs(
                date_from=date_from,
                date_to=date_to,
                match_key=match_key,
                competition_code=competition_code,
                limit=limit,
            )
        )

        scored_runs_total = len(rows)
        runs: List[dict] = []
        for r in rows:
            identity = extract_settlement_identity(
                snapshot_json=r.snapshot_json,
                run_home_team=r.home_team,
                run_away_team=r.away_team,
                run_kickoff_utc=r.kickoff_utc,
            )
            if identity is None:
                # Cannot join without identity; keep fail-soft by skipping evaluation for this run.
                continue

            pred = r.prediction_json or {}
            if isinstance(pred, dict) and pred.get("analysis_mode") == "analysis_only":
                continue

            best = pred.get("best_market") if isinstance(pred, dict) else None

            runs.append(
                {
                    "run_id": r.run_id,
                    "match_key": r.match_key,
                    "match_date": identity.match_date,
                    "home_team": identity.home_team,
                    "away_team": identity.away_team,
                    "identity_source": identity.source,
                    "best_market": best,
                    "report": r.report_json,
                    "scoring_warnings": list(r.scoring_warnings or []),
                    "competition_code": r.competition_code,
                    "competition_name": _competition_name_from_snapshot(r.snapshot_json),
                }
            )

        report = evaluate_best_market_runs(
            runs,
            scored_runs_total=scored_runs_total,
            exact_lookup=self._repo.fetch_match_result_exact,
            date_lookup=self._repo.fetch_match_results_for_date,
        )
        report["filters"] = {
            "date_from": date_from,
            "date_to": date_to,
            "match_key": match_key,
            "competition_code": competition_code,
            "limit": limit,
        }
        report["data_notes"] = {
            "no_future_leakage": True,
            "settlement_identity_contract": SETTLEMENT_IDENTITY_CONTRACT,
            "source_tables": [
                "analysis_runs_v2",
                "analysis_predictions_v2",
                "analysis_build_reports_v2",
                "analysis_snapshots_v2",
                "match_results",
            ],
        }
        return report

    def close(self) -> None:
        self._repo.close()

