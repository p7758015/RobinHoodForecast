"""
Repository for persistence of analysis runs (v2) with lineage:

MergedMatchAnalysisContext -> MatchAnalysisSnapshotV2 (+ BuildReport) -> MatchPredictionResultV2.

Additive-only: does not modify existing v1/v2 prediction tables usage.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from football_agent.analysis_merge.models import MergedMatchAnalysisContext
from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, MatchPredictionResultV2
from football_agent.normalizers.merged_snapshot_builder_v2 import (
    BuildReport,
    _competition_code_from_flashscore,
)
from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs
from football_agent.storage.match_key import build_match_key_from_merged
from football_agent.storage.sqlite_runtime import open_sqlite_connection
from football_agent.storage.v2_database import (
    CREATE_ANALYSIS_BUILD_REPORTS_V2,
    CREATE_ANALYSIS_MERGED_CONTEXT_V2,
    CREATE_ANALYSIS_PREDICTIONS_V2,
    CREATE_ANALYSIS_RUNS_V2,
    CREATE_ANALYSIS_SNAPSHOTS_V2,
    CREATE_MATCH_RESULTS,
    CREATE_V2_PREDICTIONS,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _competition_code_from_merged(merged: MergedMatchAnalysisContext) -> str:
    """Stable code using the same derivation as MergedSnapshotBuilderV2."""
    name = (
        merged.flashscore_facts.meta.competition_name
        or merged.headline.competition_name
        or ""
    )
    return _competition_code_from_flashscore(name)


def _run_header_from_merged(merged: MergedMatchAnalysisContext) -> tuple[str, Optional[str], str, str]:
    """Extract run header fields aligned with snapshot builder inputs."""
    kickoff = merged.headline.kickoff_utc or merged.flashscore_facts.meta.kickoff_utc
    kickoff_iso = kickoff.isoformat() if kickoff else None
    return (
        _competition_code_from_merged(merged),
        kickoff_iso,
        merged.headline.home_team,
        merged.headline.away_team,
    )


@dataclass(frozen=True)
class AnalysisRunBundleV2:
    run_id: str
    run_status: str
    match_key: str
    created_at_utc: datetime

    merged_context: Optional[dict] = None
    snapshot: Optional[dict] = None
    build_report: Optional[dict] = None
    prediction: Optional[dict] = None


class AnalysisRunRepositoryV2:
    def __init__(self, db_path: str | Path | None = None) -> None:
        ensure_runtime_dirs()
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.conn = open_sqlite_connection(self.db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # Keep existing schema tables available (no behavior changes).
        self.conn.execute(CREATE_MATCH_RESULTS)
        self.conn.execute(CREATE_V2_PREDICTIONS)
        # Additive run persistence tables.
        self.conn.execute(CREATE_ANALYSIS_RUNS_V2)
        self.conn.execute(CREATE_ANALYSIS_MERGED_CONTEXT_V2)
        self.conn.execute(CREATE_ANALYSIS_SNAPSHOTS_V2)
        self.conn.execute(CREATE_ANALYSIS_BUILD_REPORTS_V2)
        self.conn.execute(CREATE_ANALYSIS_PREDICTIONS_V2)
        self.conn.commit()

    # ---------------------------------------------------------------------
    # Write API (staged)
    # ---------------------------------------------------------------------

    def create_run_from_merged(self, merged: MergedMatchAnalysisContext, *, commit: bool = True) -> str:
        run_id = str(uuid.uuid4())
        match_key = build_match_key_from_merged(merged)
        now = _utc_now().isoformat()

        merged_id = str(uuid.uuid4())
        headline_json = merged.headline.model_dump_json()
        merged_json = merged.model_dump_json()
        blocks_present = json.dumps(list(merged.provenance.blocks_present))
        missing_blocks = json.dumps(list(merged.provenance.missing_blocks))
        warnings = json.dumps(list(merged.provenance.warnings))

        self.conn.execute(
            """
            INSERT INTO analysis_merged_context_v2
            (id, created_at_utc, match_key, headline_json, merged_json,
             blocks_present_json, missing_blocks_json, warnings_json)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                merged_id,
                now,
                match_key,
                headline_json,
                merged_json,
                blocks_present,
                missing_blocks,
                warnings,
            ),
        )

        comp_code, kickoff_iso, home_team, away_team = _run_header_from_merged(merged)

        self.conn.execute(
            """
            INSERT INTO analysis_runs_v2
            (run_id, created_at_utc, run_status, match_key,
             merged_context_id,
             merge_warnings_count, missing_blocks_count,
             openclaw_link_strategy, odds_link_strategy,
             competition_code, kickoff_utc, home_team, away_team)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                now,
                "merged_only",
                match_key,
                merged_id,
                len(merged.provenance.warnings),
                len(merged.provenance.missing_blocks),
                str(merged.provenance.match_link_strategy),
                str(merged.provenance.odds_link_strategy),
                comp_code,
                kickoff_iso,
                home_team,
                away_team,
            ),
        )

        if commit:
            self.conn.commit()
        return run_id

    def attach_snapshot_and_report(
        self,
        run_id: str,
        *,
        merged: MergedMatchAnalysisContext,
        snapshot: MatchAnalysisSnapshotV2,
        report: BuildReport,
        commit: bool = True,
    ) -> None:
        match_key = build_match_key_from_merged(merged)
        now = _utc_now().isoformat()

        snapshot_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO analysis_snapshots_v2
            (id, created_at_utc, match_key, match_id, snapshot_json)
            VALUES (?,?,?,?,?)
            """,
            (
                snapshot_id,
                now,
                match_key,
                snapshot.match_meta.match_id,
                snapshot.model_dump_json(),
            ),
        )

        report_id = str(uuid.uuid4())
        report_dict = asdict(report)
        self.conn.execute(
            """
            INSERT INTO analysis_build_reports_v2
            (id, created_at_utc, run_id, match_key, report_json,
             merge_warnings_json, merge_missing_blocks_json,
             openclaw_link_strategy, odds_link_strategy,
             builder_warnings_json, id_generation_notes_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                report_id,
                now,
                run_id,
                match_key,
                json.dumps(report_dict, ensure_ascii=False),
                json.dumps(report.merge_warnings, ensure_ascii=False),
                json.dumps(report.merge_missing_blocks, ensure_ascii=False),
                report.openclaw_link_strategy,
                report.odds_link_strategy,
                json.dumps(report.builder_warnings, ensure_ascii=False),
                json.dumps(report.id_generation_notes, ensure_ascii=False),
            ),
        )

        kickoff_iso = (
            snapshot.match_meta.match_date_utc.isoformat()
            if snapshot.match_meta.match_date_utc
            else None
        )
        self.conn.execute(
            """
            UPDATE analysis_runs_v2
               SET run_status=?,
                   snapshot_id=?,
                   snapshot_match_id=?,
                   competition_code=?,
                   kickoff_utc=?,
                   home_team=?,
                   away_team=?
             WHERE run_id=?
            """,
            (
                "snapshot_built",
                snapshot_id,
                snapshot.match_meta.match_id,
                snapshot.match_meta.competition_code,
                kickoff_iso,
                snapshot.match_meta.home_team.name,
                snapshot.match_meta.away_team.name,
                run_id,
            ),
        )
        if commit:
            self.conn.commit()

    def attach_prediction(
        self,
        run_id: str,
        *,
        prediction: MatchPredictionResultV2,
        scoring_warnings: list[str],
        commit: bool = True,
    ) -> None:
        now = _utc_now().isoformat()
        pred_id = str(uuid.uuid4())

        best = prediction.best_market
        express = prediction.express_safety

        self.conn.execute(
            """
            INSERT INTO analysis_predictions_v2
            (id, created_at_utc, run_id, prediction_json,
             best_market_key, best_market_prob, best_market_book_odds,
             express_safety_class, allow_for_express, scoring_warnings_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pred_id,
                now,
                run_id,
                prediction.model_dump_json(),
                best.market_key if best else None,
                float(best.probability) if best else None,
                float(best.book_odds) if (best and best.book_odds is not None) else None,
                express.safety_class.value if express else None,
                1 if (express and express.allow_for_express) else 0,
                json.dumps(list(scoring_warnings or []), ensure_ascii=False),
            ),
        )

        self.conn.execute(
            "UPDATE analysis_runs_v2 SET run_status=?, prediction_id=? WHERE run_id=?",
            ("scored", pred_id, run_id),
        )
        if commit:
            self.conn.commit()

    def persist_scored_run_atomic(
        self,
        *,
        merged: MergedMatchAnalysisContext,
        snapshot: MatchAnalysisSnapshotV2,
        report: BuildReport,
        prediction: MatchPredictionResultV2,
        scoring_warnings: list[str],
    ) -> str:
        """
        Atomically persist merged -> snapshot/report -> prediction.

        Rolls back all writes on any intermediate failure (no orphan rows).
        """
        self.conn.execute("BEGIN")
        try:
            run_id = self.create_run_from_merged(merged, commit=False)
            self.attach_snapshot_and_report(
                run_id,
                merged=merged,
                snapshot=snapshot,
                report=report,
                commit=False,
            )
            self.attach_prediction(
                run_id,
                prediction=prediction,
                scoring_warnings=scoring_warnings,
                commit=False,
            )
            self.conn.commit()
            return run_id
        except Exception:
            self.conn.rollback()
            raise

    # ---------------------------------------------------------------------
    # Read API
    # ---------------------------------------------------------------------

    def load_run(self, run_id: str) -> Optional[AnalysisRunBundleV2]:
        row = self.conn.execute("SELECT * FROM analysis_runs_v2 WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None

        merged = None
        if row["merged_context_id"]:
            m = self.conn.execute(
                "SELECT merged_json FROM analysis_merged_context_v2 WHERE id=?",
                (row["merged_context_id"],),
            ).fetchone()
            merged = json.loads(m["merged_json"]) if m else None

        snap = None
        if row["snapshot_id"]:
            s = self.conn.execute(
                "SELECT snapshot_json FROM analysis_snapshots_v2 WHERE id=?",
                (row["snapshot_id"],),
            ).fetchone()
            snap = json.loads(s["snapshot_json"]) if s else None

        rep = None
        r = self.conn.execute(
            "SELECT report_json FROM analysis_build_reports_v2 WHERE run_id=? ORDER BY created_at_utc DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        rep = json.loads(r["report_json"]) if r else None

        pred = None
        if row["prediction_id"]:
            p = self.conn.execute(
                "SELECT prediction_json FROM analysis_predictions_v2 WHERE id=?",
                (row["prediction_id"],),
            ).fetchone()
            pred = json.loads(p["prediction_json"]) if p else None

        created_at = datetime.fromisoformat(row["created_at_utc"])
        return AnalysisRunBundleV2(
            run_id=str(row["run_id"]),
            run_status=str(row["run_status"]),
            match_key=str(row["match_key"]),
            created_at_utc=created_at,
            merged_context=merged,
            snapshot=snap,
            build_report=rep,
            prediction=pred,
        )

    def load_latest_run_for_match_key(self, match_key: str) -> Optional[AnalysisRunBundleV2]:
        row = self.conn.execute(
            "SELECT run_id FROM analysis_runs_v2 WHERE match_key=? ORDER BY created_at_utc DESC LIMIT 1",
            (match_key,),
        ).fetchone()
        return self.load_run(str(row["run_id"])) if row else None

    def close(self) -> None:
        self.conn.close()

