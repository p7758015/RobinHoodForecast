"""
Operational orchestration for eval waves: accumulate → results → settlement stats → report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from football_agent.eval_pool.accumulate import accumulate_league_pool
from football_agent.eval_pool.calibration_report import collect_settled_pool_eval_records
from football_agent.eval_pool.report import LeagueEvalPoolReporter
from football_agent.eval_pool.settle import settle_league_pool_from_flashscore
from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.eval_pool.wave_predictions import collect_wave_predictions, predictions_to_json
from football_agent.eval_pool.wave_summary import (
    build_wave_cli_summary,
    write_wave_artifacts,
)
from football_agent.paths import DEFAULT_DB_PATH, EVAL_WAVE_REPORTS_DIR, ensure_runtime_dirs
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2

logger = logging.getLogger(__name__)


@dataclass
class EvalWaveRunner:
    manifest: EvalWaveManifest
    db_path: str | Path = DEFAULT_DB_PATH
    scraper_url: Optional[str] = None
    skip_openclaw: bool = False
    use_discovery_fallback: Optional[bool] = None
    output_dir: Path = EVAL_WAVE_REPORTS_DIR
    _accumulate_fn: Callable[..., Dict[str, Any]] = field(default=accumulate_league_pool, repr=False)
    _update_results_fn: Callable[..., Dict[str, Any]] = field(
        default=settle_league_pool_from_flashscore,
        repr=False,
    )

    def _wave_meta(self) -> Dict[str, Any]:
        return {
            "wave_name": self.manifest.wave_name,
            "label": self.manifest.label,
            "date_from": self.manifest.date_from,
            "date_to": self.manifest.date_to,
            "league_keys": list(self.manifest.league_keys),
            "expected_matches": self.manifest.expected_matches,
            "notes": dict(self.manifest.notes),
        }

    def accumulate_wave(self) -> Dict[str, Any]:
        logger.info("wave accumulate start %s", self.manifest.wave_name)
        return self._accumulate_fn(
            date_from=self.manifest.date_from,
            date_to=self.manifest.date_to,
            league_keys=list(self.manifest.league_keys),
            db_path=self.db_path,
            scraper_url=self.scraper_url,
            skip_openclaw=self.skip_openclaw,
            use_discovery_fallback=self.use_discovery_fallback,
            expected_matches=self.manifest.expected_matches,
        )

    def update_results(self) -> Dict[str, Any]:
        """Fetch finished match scores from Flashscore into match_results."""
        logger.info("wave update-results start %s", self.manifest.wave_name)
        try:
            return self._update_results_fn(
                date_from=self.manifest.date_from,
                date_to=self.manifest.date_to,
                league_keys=list(self.manifest.league_keys),
                db_path=self.db_path,
                scraper_url=self.scraper_url,
            )
        except Exception as exc:
            logger.exception("update-results failed: %s", exc)
            return {
                "pipeline": "league_eval_pool_settle",
                "status": "failed",
                "error": str(exc),
                "results_saved": 0,
            }

    def settle_wave(self) -> Dict[str, Any]:
        """
        Join persisted predictions with match_results (read-only).

        Does not write settlement state — uses existing evaluation join contract.
        """
        logger.info("wave settlement stats %s", self.manifest.wave_name)
        repo = EvaluationRepositoryV2(db_path=self.db_path)
        try:
            rows = list(
                repo.iter_scored_runs(
                    date_from=self.manifest.date_from,
                    date_to=f"{self.manifest.date_to}T23:59:59",
                    limit=10000,
                )
            )
            records, stats = collect_settled_pool_eval_records(
                rows,
                allowed_keys=tuple(self.manifest.league_keys),
                repo=repo,
            )
            wins = sum(1 for r in records if r.outcome)
            losses = len(records) - wins
            hit_rate = round(wins / len(records), 4) if records else None
            return {
                "pipeline": "eval_wave_settlement_stats",
                "wave_name": self.manifest.wave_name,
                "league_scored_runs": stats.get("league_scored", 0),
                "settled_evaluable": len(records),
                "unsettled": stats.get("unsettled", 0),
                "parked_skipped": stats.get("parked_skipped", 0),
                "identity_missing": stats.get("identity_missing", 0),
                "no_best_market": stats.get("no_best_market", 0),
                "wins": wins,
                "losses": losses,
                "hit_rate": hit_rate,
                "collection_stats": stats,
            }
        finally:
            repo.close()

    def _build_reports(self) -> tuple[Dict[str, Any], Dict[str, Any]]:
        reporter = LeagueEvalPoolReporter(db_path=self.db_path)
        try:
            coverage = reporter.build_report(
                league_keys=list(self.manifest.league_keys),
                date_from=self.manifest.date_from,
                date_to=f"{self.manifest.date_to}T23:59:59",
            )
            calibration = reporter.build_calibration_review(
                league_keys=list(self.manifest.league_keys),
                date_from=self.manifest.date_from,
                date_to=f"{self.manifest.date_to}T23:59:59",
            )
            return coverage, calibration
        finally:
            reporter.close()

    def report_wave(
        self,
        *,
        write_artifacts: bool = True,
        accumulate: Optional[Dict[str, Any]] = None,
        update_results: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logger.info("wave report %s", self.manifest.wave_name)
        coverage, calibration = self._build_reports()
        settlement = self.settle_wave()
        prediction_views = collect_wave_predictions(self.manifest, db_path=self.db_path)
        payload: Dict[str, Any] = {
            "wave": self._wave_meta(),
            "accumulate": accumulate,
            "update_results": update_results,
            "settlement": settlement,
            "coverage_report": coverage,
            "calibration": calibration,
            "predictions": predictions_to_json(prediction_views),
            "prediction_views": prediction_views,
        }

        output_paths: Dict[str, str] = {}
        if write_artifacts:
            ensure_runtime_dirs()
            output_paths = write_wave_artifacts(
                self.manifest,
                payload,
                output_dir=self.output_dir,
            )
            payload["output_paths"] = output_paths

        payload["cli_summary"] = build_wave_cli_summary(
            self.manifest,
            accumulate=accumulate,
            update_results=update_results,
            settlement=settlement,
            coverage_report=coverage,
            calibration=calibration,
            output_paths=output_paths or None,
        )
        return payload

    def full_wave(self, *, write_artifacts: bool = True) -> Dict[str, Any]:
        """Run full operational cycle fail-soft."""
        stages: Dict[str, Any] = {}

        try:
            stages["accumulate"] = self.accumulate_wave()
        except Exception as exc:
            logger.exception("accumulate-wave failed")
            stages["accumulate"] = {"status": "failed", "error": str(exc)}

        try:
            stages["update_results"] = self.update_results()
        except Exception as exc:
            logger.exception("update-results failed")
            stages["update_results"] = {"status": "failed", "error": str(exc)}

        try:
            stages["settlement"] = self.settle_wave()
        except Exception as exc:
            logger.exception("settle-wave stats failed")
            stages["settlement"] = {"status": "failed", "error": str(exc)}

        try:
            coverage, calibration = self._build_reports()
            stages["coverage_report"] = coverage
            stages["calibration"] = calibration
            stages["prediction_views"] = collect_wave_predictions(self.manifest, db_path=self.db_path)
        except Exception as exc:
            logger.exception("report-wave failed")
            stages["coverage_report"] = {"status": "failed", "error": str(exc)}
            stages["calibration"] = {"status": "failed", "error": str(exc)}

        payload = {
            "wave": self._wave_meta(),
            "accumulate": stages.get("accumulate"),
            "update_results": stages.get("update_results"),
            "settlement": stages.get("settlement"),
            "coverage_report": stages.get("coverage_report"),
            "calibration": stages.get("calibration"),
            "predictions": predictions_to_json(stages.get("prediction_views") or []),
            "prediction_views": stages.get("prediction_views") or [],
        }

        output_paths: Dict[str, str] = {}
        if write_artifacts:
            ensure_runtime_dirs()
            output_paths = write_wave_artifacts(self.manifest, payload, output_dir=self.output_dir)

        cli_summary = build_wave_cli_summary(
            self.manifest,
            accumulate=stages.get("accumulate"),
            update_results=stages.get("update_results"),
            settlement=stages.get("settlement"),
            coverage_report=stages.get("coverage_report") if isinstance(stages.get("coverage_report"), dict) else None,
            calibration=stages.get("calibration") if isinstance(stages.get("calibration"), dict) else None,
            output_paths=output_paths or None,
        )

        return {
            "wave_name": self.manifest.wave_name,
            "stages": stages,
            "cli_summary": cli_summary,
            "output_paths": output_paths,
            "payload": payload,
        }
