"""
League eval-pool reporting over persisted runs + settlement.

Reuses ``OfflineEvaluationServiceV2`` for hit-rate/ROI while adding pool-specific
coverage slices (parked share, confidence, odds, best-market distribution).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent.eval_pool.scope import LOW_CONFIDENCE_THRESHOLD, filter_pool_keys, resolve_pool_entry
from football_agent.offline.evaluation_v2 import extract_settlement_identity
from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs
from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2
from football_agent.storage.sqlite_runtime import open_sqlite_connection


def _prediction_dict(pred_json: Optional[str | dict]) -> dict:
    if pred_json is None:
        return {}
    if isinstance(pred_json, dict):
        return pred_json
    try:
        parsed = json.loads(pred_json)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _snapshot_meta(snapshot_json: Optional[str | dict]) -> dict:
    if snapshot_json is None:
        return {}
    data = snapshot_json if isinstance(snapshot_json, dict) else {}
    if not isinstance(data, dict):
        try:
            data = json.loads(snapshot_json)
        except (json.JSONDecodeError, TypeError):
            return {}
    meta = data.get("match_meta")
    return meta if isinstance(meta, dict) else {}


def _in_pool_scope(
    *,
    competition_name: Optional[str],
    competition_country: Optional[str],
    allowed_keys: Sequence[str],
) -> bool:
    entry = resolve_pool_entry(competition_name, competition_country)
    return entry is not None and entry.key in allowed_keys


def _count_odds_markets(snapshot_json: Optional[dict]) -> int:
    if not isinstance(snapshot_json, dict):
        return 0
    odds = snapshot_json.get("odds")
    if not isinstance(odds, dict):
        return 0
    fields = (
        "home_win",
        "draw",
        "away_win",
        "home_not_lose",
        "away_not_lose",
        "btts_yes",
        "home_team_to_score",
        "away_team_to_score",
        "over_15",
    )
    return sum(1 for name in fields if odds.get(name) is not None)


class LeagueEvalPoolReporter:
    def __init__(self, *, db_path: str | Path | None = None) -> None:
        ensure_runtime_dirs()
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        self._repo = EvaluationRepositoryV2(db_path=self.db_path)

    def build_report(
        self,
        *,
        league_keys: Optional[Sequence[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        pool_entries = filter_pool_keys(league_keys)
        allowed_keys = tuple(e.key for e in pool_entries)
        allowed_codes = {e.registry_code for e in pool_entries}

        rows = list(
            self._repo.iter_scored_runs(
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        )

        pool_runs: List[dict] = []
        parked_runs: List[dict] = []
        league_scored_runs: List[dict] = []
        confidence_values: List[float] = []
        best_market_counts: Counter[str] = Counter()
        odds_coverage = 0
        low_confidence = 0

        conn = open_sqlite_connection(self.db_path)
        try:
            all_persisted = conn.execute(
                "SELECT COUNT(*) FROM analysis_runs_v2 WHERE run_status='scored'"
            ).fetchone()[0]
        finally:
            conn.close()

        for row in rows:
            snap = row.snapshot_json or {}
            meta = _snapshot_meta(snap)
            comp_name = meta.get("competition_name") or row.competition_code
            comp_country = meta.get("country")
            if not _in_pool_scope(
                competition_name=str(comp_name) if comp_name else None,
                competition_country=str(comp_country) if comp_country else None,
                allowed_keys=allowed_keys,
            ):
                continue

            pred = row.prediction_json or {}
            analysis_mode = pred.get("analysis_mode") if isinstance(pred, dict) else None
            route = None
            if isinstance(pred, dict) and pred.get("parked_context"):
                route = "parked"
            elif isinstance(row.report_json, dict):
                route = (row.report_json or {}).get("scorer_route")

            entry = resolve_pool_entry(
                str(comp_name) if comp_name else None,
                str(comp_country) if comp_country else None,
            )
            pool_key = entry.key if entry else "unknown"

            item = {
                "run_id": row.run_id,
                "pool_key": pool_key,
                "competition_code": row.competition_code,
                "competition_name": comp_name,
                "analysis_mode": analysis_mode,
            }
            pool_runs.append(item)

            if analysis_mode == "analysis_only":
                parked_runs.append(item)
                continue

            league_scored_runs.append(item)
            conf = float(pred.get("overall_confidence_score") or 0.0) if isinstance(pred, dict) else 0.0
            confidence_values.append(conf)
            if conf < LOW_CONFIDENCE_THRESHOLD:
                low_confidence += 1
            if _count_odds_markets(snap) > 0:
                odds_coverage += 1
            best = pred.get("best_market") if isinstance(pred, dict) else None
            if isinstance(best, dict):
                key = str(best.get("market_key") or best.get("key") or "unknown")
                best_market_counts[key] += 1

        settled_count = 0
        for item in league_scored_runs:
            run_row = next((r for r in rows if r.run_id == item["run_id"]), None)
            if run_row is None:
                continue
            identity = extract_settlement_identity(
                snapshot_json=run_row.snapshot_json,
                run_home_team=run_row.home_team,
                run_away_team=run_row.away_team,
                run_kickoff_utc=run_row.kickoff_utc,
            )
            if identity is None:
                continue
            settled = self._repo.resolve_settlement(
                identity.match_date,
                identity.home_team,
                identity.away_team,
            )
            if settled.resolved:
                settled_count += 1

        eval_codes = list(allowed_codes) if len(allowed_codes) == 1 else None
        eval_svc = OfflineEvaluationServiceV2(db_path=self.db_path)
        try:
            evaluation = eval_svc.evaluate(
                date_from=date_from,
                date_to=date_to,
                competition_code=eval_codes[0] if eval_codes else None,
                limit=limit,
            )
        finally:
            eval_svc.close()

        confidence_dist = {
            "lt_0_45": sum(1 for c in confidence_values if c < 0.45),
            "0_45_0_60": sum(1 for c in confidence_values if 0.45 <= c < 0.60),
            "0_60_0_75": sum(1 for c in confidence_values if 0.60 <= c < 0.75),
            "gte_0_75": sum(1 for c in confidence_values if c >= 0.75),
        }

        return {
            "pipeline": "league_eval_pool_report",
            "pool": [e.key for e in pool_entries],
            "filters": {
                "date_from": date_from,
                "date_to": date_to,
                "limit": limit,
            },
            "counts": {
                "persisted_runs_total_db": int(all_persisted),
                "pool_runs": len(pool_runs),
                "league_scored_runs": len(league_scored_runs),
                "parked_or_analysis_only_in_pool": len(parked_runs),
                "settled_league_scored_runs": settled_count,
                "runs_with_odds": odds_coverage,
                "low_confidence_runs": low_confidence,
            },
            "parked_share_in_pool": (
                round(len(parked_runs) / len(pool_runs), 4) if pool_runs else 0.0
            ),
            "confidence_distribution": confidence_dist,
            "best_market_distribution": dict(best_market_counts),
            "evaluation_subset": evaluation,
        }

    def build_calibration_review(
        self,
        *,
        league_keys: Optional[Sequence[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        """Pool-scoped calibration review over settled league-scored runs."""
        from football_agent.eval_pool.calibration_report import (
            build_pool_calibration_review,
            collect_settled_pool_eval_records,
        )

        pool_entries = filter_pool_keys(league_keys)
        allowed_keys = tuple(e.key for e in pool_entries)
        rows = list(
            self._repo.iter_scored_runs(
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        )
        records, collection_stats = collect_settled_pool_eval_records(
            rows,
            allowed_keys=allowed_keys,
            repo=self._repo,
        )
        review = build_pool_calibration_review(records, collection_stats=collection_stats)
        review["pool"] = [e.key for e in pool_entries]
        review["filters"] = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        }
        return review

    def close(self) -> None:
        self._repo.close()
