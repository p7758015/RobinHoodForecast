"""
Offline evaluation service over persisted analysis runs.

No future leakage: reads only stored artifacts + final match_results.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from football_agent.offline.evaluation_v2 import evaluate_best_market_runs
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2


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

        runs: List[dict] = []
        for r in rows:
            snap = r.snapshot_json or {}
            meta = (snap.get("match_meta") or {}) if isinstance(snap, dict) else {}

            match_date_utc = meta.get("match_date_utc")
            match_date = None
            if isinstance(match_date_utc, str) and len(match_date_utc) >= 10:
                match_date = match_date_utc[:10]

            home = ((meta.get("home_team") or {}) if isinstance(meta.get("home_team"), dict) else {}).get("name")
            away = ((meta.get("away_team") or {}) if isinstance(meta.get("away_team"), dict) else {}).get("name")

            pred = r.prediction_json or {}
            best = pred.get("best_market") if isinstance(pred, dict) else None

            if not match_date or not isinstance(home, str) or not isinstance(away, str):
                # Cannot join without identity; keep fail-soft by skipping evaluation for this run.
                continue

            runs.append(
                {
                    "run_id": r.run_id,
                    "match_key": r.match_key,
                    "match_date": match_date,
                    "home_team": home,
                    "away_team": away,
                    "best_market": best,
                    "report": r.report_json,
                }
            )

        report = evaluate_best_market_runs(
            runs,
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

