"""SQLite storage for v2 predictions + additive analysis run persistence."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from football_agent.domain.models_v2 import MatchPredictionResultV2
from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs

logger = logging.getLogger(__name__)

CREATE_MATCH_RESULTS = """
CREATE TABLE IF NOT EXISTS match_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date  TEXT NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_score  INTEGER NOT NULL,
    away_score  INTEGER NOT NULL,
    settled_at  TEXT NOT NULL,
    UNIQUE(match_date, home_team, away_team)
)"""

CREATE_V2_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS v2_predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date          TEXT NOT NULL,
    competition         TEXT NOT NULL,
    home_team           TEXT NOT NULL,
    away_team           TEXT NOT NULL,
    match_id            INTEGER,
    market_key          TEXT NOT NULL,
    probability         REAL NOT NULL,
    book_odds           REAL,
    fair_odds           REAL,
    edge                REAL,
    pipeline_version    TEXT NOT NULL DEFAULT 'v2',
    season_progress     REAL,
    h2h_btts_rate       REAL,
    express_safety_class TEXT,
    overall_confidence  REAL,
    created_at          TEXT NOT NULL,
    UNIQUE(match_date, home_team, away_team, market_key)
)"""

CREATE_ANALYSIS_RUNS_V2 = """
CREATE TABLE IF NOT EXISTS analysis_runs_v2 (
    run_id              TEXT PRIMARY KEY,
    created_at_utc      TEXT NOT NULL,
    run_status          TEXT NOT NULL,
    match_key           TEXT NOT NULL,

    snapshot_match_id   INTEGER,
    competition_code    TEXT,
    kickoff_utc         TEXT,
    home_team           TEXT,
    away_team           TEXT,

    merged_context_id   TEXT,
    snapshot_id         TEXT,
    prediction_id       TEXT,

    merge_warnings_count    INTEGER DEFAULT 0,
    missing_blocks_count    INTEGER DEFAULT 0,
    openclaw_link_strategy  TEXT,
    odds_link_strategy      TEXT
)"""

CREATE_ANALYSIS_MERGED_CONTEXT_V2 = """
CREATE TABLE IF NOT EXISTS analysis_merged_context_v2 (
    id              TEXT PRIMARY KEY,
    created_at_utc  TEXT NOT NULL,
    match_key       TEXT NOT NULL,
    headline_json   TEXT NOT NULL,
    merged_json     TEXT NOT NULL,
    blocks_present_json TEXT,
    missing_blocks_json TEXT,
    warnings_json       TEXT
)"""

CREATE_ANALYSIS_SNAPSHOTS_V2 = """
CREATE TABLE IF NOT EXISTS analysis_snapshots_v2 (
    id              TEXT PRIMARY KEY,
    created_at_utc  TEXT NOT NULL,
    match_key       TEXT NOT NULL,
    match_id        INTEGER,
    snapshot_json   TEXT NOT NULL
)"""

CREATE_ANALYSIS_BUILD_REPORTS_V2 = """
CREATE TABLE IF NOT EXISTS analysis_build_reports_v2 (
    id              TEXT PRIMARY KEY,
    created_at_utc  TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    match_key       TEXT NOT NULL,
    report_json     TEXT NOT NULL,
    merge_warnings_json       TEXT,
    merge_missing_blocks_json TEXT,
    openclaw_link_strategy    TEXT,
    odds_link_strategy        TEXT,
    builder_warnings_json     TEXT,
    id_generation_notes_json  TEXT
)"""

CREATE_ANALYSIS_PREDICTIONS_V2 = """
CREATE TABLE IF NOT EXISTS analysis_predictions_v2 (
    id              TEXT PRIMARY KEY,
    created_at_utc  TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    prediction_json TEXT NOT NULL,
    best_market_key TEXT,
    best_market_prob REAL,
    best_market_book_odds REAL,
    express_safety_class TEXT,
    allow_for_express INTEGER,
    scoring_warnings_json TEXT
)"""


class V2Database:
    """v2_predictions + additive analysis run persistence (does not touch v1 writes)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        ensure_runtime_dirs()
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(CREATE_MATCH_RESULTS)
        self.conn.execute(CREATE_V2_PREDICTIONS)
        # Additive persistence tables for merged/snapshot/report/prediction lineage.
        self.conn.execute(CREATE_ANALYSIS_RUNS_V2)
        self.conn.execute(CREATE_ANALYSIS_MERGED_CONTEXT_V2)
        self.conn.execute(CREATE_ANALYSIS_SNAPSHOTS_V2)
        self.conn.execute(CREATE_ANALYSIS_BUILD_REPORTS_V2)
        self.conn.execute(CREATE_ANALYSIS_PREDICTIONS_V2)
        self.conn.commit()

    def save_match_result(
        self,
        match_date: str,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
    ) -> None:
        """Reuse shared match_results table (read by v1 settle too)."""
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO match_results
                   (match_date, home_team, away_team, home_score, away_score, settled_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    match_date,
                    home_team,
                    away_team,
                    home_score,
                    away_score,
                    datetime.utcnow().isoformat(),
                ),
            )
            self.conn.commit()
        except Exception as e:
            logger.error("save_match_result error: %s", e)

    def save_prediction_result(
        self,
        result: MatchPredictionResultV2,
        match_date: str,
        *,
        h2h_btts_rate: Optional[float] = None,
    ) -> int:
        """Persist all market_predictions for one match. Returns rows inserted."""
        meta = result.match_meta
        now = datetime.utcnow().isoformat()
        express_class = result.express_safety.safety_class.value
        inserted = 0
        cur = self.conn.cursor()
        for market in result.market_predictions:
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO v2_predictions
                    (match_date, competition, home_team, away_team, match_id,
                     market_key, probability, book_odds, fair_odds, edge,
                     pipeline_version, season_progress, h2h_btts_rate,
                     express_safety_class, overall_confidence, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        match_date,
                        meta.competition_code,
                        meta.home_team.name,
                        meta.away_team.name,
                        meta.match_id,
                        market.market_key,
                        market.probability,
                        market.book_odds,
                        market.fair_odds,
                        market.edge,
                        "v2",
                        meta.season_progress,
                        h2h_btts_rate,
                        express_class,
                        result.overall_confidence_score,
                        now,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception as e:
                logger.warning(
                    "v2_predictions skip %s vs %s %s: %s",
                    meta.home_team.name,
                    meta.away_team.name,
                    market.market_key,
                    e,
                )
        self.conn.commit()
        return inserted

    def fetch_settled_rows(self) -> List[sqlite3.Row]:
        """Join v2_predictions with match_results for offline reports."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT
                p.id,
                p.match_date,
                p.competition,
                p.home_team,
                p.away_team,
                p.market_key,
                p.probability,
                p.book_odds,
                p.fair_odds,
                p.edge,
                p.season_progress,
                p.h2h_btts_rate,
                p.express_safety_class,
                p.overall_confidence,
                r.home_score,
                r.away_score
            FROM v2_predictions p
            INNER JOIN match_results r
                ON p.match_date = r.match_date
               AND p.home_team = r.home_team
               AND p.away_team = r.away_team
            """
        )
        return cur.fetchall()

    def count_predictions(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM v2_predictions").fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        self.conn.close()
