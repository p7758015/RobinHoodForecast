"""Safe cleanup of accumulated analysis runs for a specific eval wave."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from football_agent.eval_pool.report import _in_pool_scope, _snapshot_meta
from football_agent.eval_pool.scope import resolve_pool_entry
from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.paths import DEFAULT_DB_PATH
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2
from football_agent.storage.sqlite_runtime import open_sqlite_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WaveRunRef:
    run_id: str
    match_key: str
    kickoff_date: Optional[str]
    pool_key: str
    competition_name: Optional[str]
    home_team: Optional[str]
    away_team: Optional[str]
    merged_context_id: Optional[str]
    snapshot_id: Optional[str]
    prediction_id: Optional[str]


def _kickoff_date_from_run(row, snapshot_json: Optional[dict]) -> Optional[str]:
    if row.kickoff_utc and len(str(row.kickoff_utc)) >= 10:
        return str(row.kickoff_utc)[:10]
    meta = _snapshot_meta(snapshot_json or {})
    md = meta.get("match_date_utc")
    if isinstance(md, str) and len(md) >= 10:
        return md[:10]
    return None


def collect_wave_runs(
    manifest: EvalWaveManifest,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> List[WaveRunRef]:
    """Find persisted scored runs belonging to this wave (pool + date range)."""
    allowed_keys = tuple(manifest.league_keys)
    repo = EvaluationRepositoryV2(db_path=db_path)
    refs: List[WaveRunRef] = []
    try:
        rows = list(
            repo.iter_scored_runs(
                date_from=manifest.date_from,
                date_to=f"{manifest.date_to}T23:59:59",
                limit=50000,
            )
        )
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

            kickoff_date = _kickoff_date_from_run(row, snap)
            if kickoff_date is not None:
                if kickoff_date < manifest.date_from or kickoff_date > manifest.date_to:
                    continue
            else:
                continue

            entry = resolve_pool_entry(
                str(comp_name) if comp_name else None,
                str(comp_country) if comp_country else None,
            )
            conn = repo.conn
            run_row = conn.execute(
                "SELECT merged_context_id, snapshot_id, prediction_id FROM analysis_runs_v2 WHERE run_id=?",
                (row.run_id,),
            ).fetchone()
            refs.append(
                WaveRunRef(
                    run_id=row.run_id,
                    match_key=row.match_key,
                    kickoff_date=kickoff_date,
                    pool_key=entry.key if entry else "unknown",
                    competition_name=str(comp_name) if comp_name else None,
                    home_team=row.home_team,
                    away_team=row.away_team,
                    merged_context_id=run_row["merged_context_id"] if run_row else None,
                    snapshot_id=run_row["snapshot_id"] if run_row else None,
                    prediction_id=run_row["prediction_id"] if run_row else None,
                )
            )
    finally:
        repo.close()
    return refs


def cleanup_wave_runs(
    manifest: EvalWaveManifest,
    *,
    db_path: str = DEFAULT_DB_PATH,
    dry_run: bool = True,
    include_match_results: bool = False,
) -> Dict[str, Any]:
    """
    Remove analysis runs (and related artifacts) for this wave.

    Does NOT delete unrelated historical runs outside pool/date range.
    ``match_results`` are left intact by default (safe for partial settlement).
    """
    refs = collect_wave_runs(manifest, db_path=db_path)
    summary: Dict[str, Any] = {
        "pipeline": "eval_wave_cleanup",
        "wave_name": manifest.wave_name,
        "date_from": manifest.date_from,
        "date_to": manifest.date_to,
        "league_keys": list(manifest.league_keys),
        "dry_run": dry_run,
        "runs_matched": len(refs),
        "runs_deleted": 0,
        "build_reports_deleted": 0,
        "predictions_deleted": 0,
        "snapshots_deleted": 0,
        "merged_contexts_deleted": 0,
        "match_results_deleted": 0,
        "run_ids": [r.run_id for r in refs],
    }

    if dry_run or not refs:
        return summary

    conn = open_sqlite_connection(db_path)
    try:
        if include_match_results:
            for ref in refs:
                if ref.kickoff_date and ref.home_team and ref.away_team:
                    deleted = conn.execute(
                        "DELETE FROM match_results WHERE match_date=? AND home_team=? AND away_team=?",
                        (ref.kickoff_date, ref.home_team, ref.away_team),
                    ).rowcount
                    summary["match_results_deleted"] += max(deleted, 0)

        for ref in refs:
            br = conn.execute(
                "DELETE FROM analysis_build_reports_v2 WHERE run_id=?",
                (ref.run_id,),
            ).rowcount
            summary["build_reports_deleted"] += max(br, 0)

            if ref.prediction_id:
                pr = conn.execute(
                    "DELETE FROM analysis_predictions_v2 WHERE id=?",
                    (ref.prediction_id,),
                ).rowcount
                summary["predictions_deleted"] += max(pr, 0)

            if ref.snapshot_id:
                sr = conn.execute(
                    "DELETE FROM analysis_snapshots_v2 WHERE id=?",
                    (ref.snapshot_id,),
                ).rowcount
                summary["snapshots_deleted"] += max(sr, 0)

            if ref.merged_context_id:
                mr = conn.execute(
                    "DELETE FROM analysis_merged_context_v2 WHERE id=?",
                    (ref.merged_context_id,),
                ).rowcount
                summary["merged_contexts_deleted"] += max(mr, 0)

            rr = conn.execute(
                "DELETE FROM analysis_runs_v2 WHERE run_id=?",
                (ref.run_id,),
            ).rowcount
            summary["runs_deleted"] += max(rr, 0)

        conn.commit()
    finally:
        conn.close()

    logger.info("wave cleanup done: %s", summary)
    return summary
