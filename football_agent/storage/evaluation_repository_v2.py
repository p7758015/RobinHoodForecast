"""
Read-only repository for offline evaluation over persisted analysis runs.

Important: evaluation must use only persisted artifacts (no future leakage):
- analysis_runs_v2 / analysis_predictions_v2 / analysis_build_reports_v2 / analysis_snapshots_v2
- match_results (final scores)
No ingestion, no builder rebuild, no scorer re-run.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs
from football_agent.storage.sqlite_runtime import open_sqlite_connection
from football_agent.offline.evaluation_v2 import Settlement, resolve_match_result
from football_agent.storage.v2_database import (
    CREATE_ANALYSIS_BUILD_REPORTS_V2,
    CREATE_ANALYSIS_MERGED_CONTEXT_V2,
    CREATE_ANALYSIS_PREDICTIONS_V2,
    CREATE_ANALYSIS_RUNS_V2,
    CREATE_ANALYSIS_SNAPSHOTS_V2,
    CREATE_MATCH_RESULTS,
    CREATE_V2_PREDICTIONS,
)


@dataclass(frozen=True)
class EvaluationRunRow:
    run_id: str
    match_key: str
    created_at_utc: str
    run_status: str
    competition_code: Optional[str]
    kickoff_utc: Optional[str]
    home_team: Optional[str]
    away_team: Optional[str]

    snapshot_json: Optional[dict]
    report_json: Optional[dict]
    prediction_json: Optional[dict]
    scoring_warnings: List[str]


class EvaluationRepositoryV2:
    def __init__(self, db_path: str | Path | None = None) -> None:
        ensure_runtime_dirs()
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.conn = open_sqlite_connection(self.db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # Ensure tables exist (read-only usage; additive schema already created elsewhere).
        self.conn.execute(CREATE_MATCH_RESULTS)
        self.conn.execute(CREATE_V2_PREDICTIONS)
        self.conn.execute(CREATE_ANALYSIS_RUNS_V2)
        self.conn.execute(CREATE_ANALYSIS_MERGED_CONTEXT_V2)
        self.conn.execute(CREATE_ANALYSIS_SNAPSHOTS_V2)
        self.conn.execute(CREATE_ANALYSIS_BUILD_REPORTS_V2)
        self.conn.execute(CREATE_ANALYSIS_PREDICTIONS_V2)
        self.conn.commit()

    def iter_scored_runs(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        match_key: Optional[str] = None,
        competition_code: Optional[str] = None,
        limit: int = 1000,
    ) -> Iterable[EvaluationRunRow]:
        where: list[str] = ["run_status='scored'"]
        params: list[Any] = []

        if match_key:
            where.append("match_key=?")
            params.append(match_key)
        if competition_code:
            where.append("competition_code=?")
            params.append(competition_code)
        if date_from:
            where.append("kickoff_utc >= ?")
            params.append(date_from)
        if date_to:
            # naive inclusive day: caller can pass end-of-day; keep simple
            where.append("kickoff_utc <= ?")
            params.append(date_to)

        sql = "SELECT * FROM analysis_runs_v2"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at_utc DESC"
        sql += " LIMIT ?"
        params.append(int(limit))

        cur = self.conn.execute(sql, tuple(params))
        for row in cur.fetchall():
            snap = self._load_json_by_id("analysis_snapshots_v2", row["snapshot_id"], "snapshot_json") if row["snapshot_id"] else None
            rep = self._load_latest_report_json(row["run_id"])
            pred = self._load_json_by_id("analysis_predictions_v2", row["prediction_id"], "prediction_json") if row["prediction_id"] else None
            scoring_warnings = self._load_scoring_warnings(row["prediction_id"]) if row["prediction_id"] else []

            yield EvaluationRunRow(
                run_id=str(row["run_id"]),
                match_key=str(row["match_key"]),
                created_at_utc=str(row["created_at_utc"]),
                run_status=str(row["run_status"]),
                competition_code=row["competition_code"],
                kickoff_utc=row["kickoff_utc"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                snapshot_json=snap,
                report_json=rep,
                prediction_json=pred,
                scoring_warnings=scoring_warnings,
            )

    def fetch_match_results_for_date(self, match_date: str) -> List[dict]:
        """All ``match_results`` rows for a UTC kickoff date (normalized fallback scan)."""
        cur = self.conn.execute(
            "SELECT match_date, home_team, away_team, home_score, away_score FROM match_results WHERE match_date=?",
            (match_date,),
        )
        return [dict(r) for r in cur.fetchall()]

    def fetch_match_result_exact(self, match_date: str, home_team: str, away_team: str) -> Optional[dict]:
        """Primary settlement join: raw (match_date, home_team, away_team) SQL equality."""
        row = self.conn.execute(
            """
            SELECT match_date, home_team, away_team, home_score, away_score
              FROM match_results
             WHERE match_date=? AND home_team=? AND away_team=?
            """,
            (match_date, home_team, away_team),
        ).fetchone()
        return dict(row) if row else None

    def resolve_settlement(
        self,
        match_date: str,
        home_team: str,
        away_team: str,
    ) -> Settlement:
        """
        Resolve final scores for a settlement identity.

        Delegates to :func:`offline.evaluation_v2.resolve_match_result` using this
        repository's exact + per-date lookups (see ``SETTLEMENT_IDENTITY_CONTRACT``).
        """
        return resolve_match_result(
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            exact_lookup=self.fetch_match_result_exact,
            date_lookup=self.fetch_match_results_for_date,
        )

    def close(self) -> None:
        self.conn.close()

    # -------------------------
    # Internals
    # -------------------------

    def _load_json_by_id(self, table: str, row_id: str, col: str) -> Optional[dict]:
        row = self.conn.execute(f"SELECT {col} FROM {table} WHERE id=?", (row_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[col])
        except Exception:
            return None

    def _load_latest_report_json(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT report_json FROM analysis_build_reports_v2 WHERE run_id=? ORDER BY created_at_utc DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["report_json"])
        except Exception:
            return None

    def _load_scoring_warnings(self, prediction_id: str) -> List[str]:
        row = self.conn.execute(
            "SELECT scoring_warnings_json FROM analysis_predictions_v2 WHERE id=?",
            (prediction_id,),
        ).fetchone()
        if not row or not row["scoring_warnings_json"]:
            return []
        try:
            parsed = json.loads(row["scoring_warnings_json"])
            return list(parsed) if isinstance(parsed, list) else []
        except Exception:
            return []

