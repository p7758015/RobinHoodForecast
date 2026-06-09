import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from football_agent.domain.models import MatchAnalysisResult
from football_agent.paths import DEFAULT_DB_PATH, ensure_runtime_dirs

logger = logging.getLogger(__name__)

CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date      TEXT NOT NULL,
    competition     TEXT NOT NULL,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    market          TEXT NOT NULL,
    probability     REAL NOT NULL,
    odds            REAL,
    season_progress REAL,
    motivation_home REAL,
    motivation_away REAL,
    form_home       REAL,
    form_away       REAL,
    coach_home      REAL,
    coach_away      REAL,
    h2h_btts_rate   REAL,
    h2h_total       INTEGER,
    is_express      INTEGER DEFAULT 0,
    express_id      TEXT,
    created_at      TEXT NOT NULL
)"""

CREATE_RESULTS = """
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

CREATE_OUTCOMES = """
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    result        TEXT NOT NULL,
    settled_at    TEXT NOT NULL
)"""


class Database:
    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        ensure_runtime_dirs()
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        c = self.conn.cursor()
        c.execute(CREATE_PREDICTIONS)
        c.execute(CREATE_RESULTS)
        c.execute(CREATE_OUTCOMES)
        self.conn.commit()

    def save_predictions(
        self,
        results: List[MatchAnalysisResult],
        is_express: bool = False,
        express_id: Optional[str] = None,
    ) -> None:
        c = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        for r in results:
            m = r.best_market
            date_str = r.match.utc_date.strftime("%Y-%m-%d")
            # Проверка на дубль
            c.execute(
                "SELECT id FROM predictions WHERE match_date=? AND home_team=? AND away_team=? AND market=?",
                (date_str, r.match.home_team.name, r.match.away_team.name, m.market),
            )
            if c.fetchone():
                continue
            c.execute(
                """
                INSERT INTO predictions
                (match_date, competition, home_team, away_team, market, probability, odds,
                 season_progress, motivation_home, motivation_away, form_home, form_away,
                 coach_home, coach_away, h2h_btts_rate, h2h_total, is_express, express_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    date_str,
                    r.match.competition_code,
                    r.match.home_team.name,
                    r.match.away_team.name,
                    m.market,
                    m.probability,
                    m.odds,
                    r.season_progress,
                    r.home_analysis.motivation,
                    r.away_analysis.motivation,
                    r.home_analysis.form,
                    r.away_analysis.form,
                    r.home_analysis.coach_strength,
                    r.away_analysis.coach_strength,
                    r.h2h.btts_rate,
                    r.h2h.total_matches,
                    1 if is_express else 0,
                    express_id,
                    now,
                ),
            )
        self.conn.commit()

    def save_match_result(self, match_date, home_team, away_team, home_score, away_score):
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO match_results
                   (match_date, home_team, away_team, home_score, away_score, settled_at)
                   VALUES (?,?,?,?,?,?)""",
                (match_date, home_team, away_team, home_score, away_score, datetime.utcnow().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"save_match_result error: {e}")

    def settle_predictions(self) -> int:
        """Определяет WIN/LOSS для всех незакрытых прогнозов у которых есть результат."""
        c = self.conn.cursor()
        c.execute(
            """
            SELECT p.id, p.home_team, p.away_team, p.match_date, p.market,
                   r.home_score, r.away_score
            FROM predictions p
            JOIN match_results r ON p.match_date=r.match_date
                AND p.home_team=r.home_team AND p.away_team=r.away_team
            WHERE p.id NOT IN (SELECT prediction_id FROM prediction_outcomes)
        """
        )
        rows = c.fetchall()

        def is_win(market, hs, as_):
            if market == "HOME_WIN":
                return hs > as_
            if market == "AWAY_WIN":
                return as_ > hs
            if market == "HOME_NOT_LOSE":
                return hs >= as_
            if market == "AWAY_NOT_LOSE":
                return as_ >= hs
            if market == "BTTS_YES":
                return hs >= 1 and as_ >= 1
            return False

        now = datetime.utcnow().isoformat()
        count = 0
        for row in rows:
            result = "WIN" if is_win(row["market"], row["home_score"], row["away_score"]) else "LOSS"
            c.execute(
                "INSERT INTO prediction_outcomes (prediction_id, result, settled_at) VALUES (?,?,?)",
                (row["id"], result, now),
            )
            count += 1
        self.conn.commit()
        return count

    def get_accuracy_report(self) -> dict:
        c = self.conn.cursor()

        def winrate(rows):
            total = len(rows)
            wins = sum(1 for r in rows if r["result"] == "WIN")
            return {"total": total, "wins": wins, "winrate": round(wins / total, 3) if total else 0.0}

        c.execute(
            """
            SELECT p.market, p.probability, p.odds, p.competition,
                   p.season_progress, p.h2h_btts_rate, o.result
            FROM predictions p
            JOIN prediction_outcomes o ON p.id = o.prediction_id
        """
        )
        all_rows = c.fetchall()
        if not all_rows:
            return {"overall": {"total": 0, "wins": 0, "winrate": 0.0}}

        report = {"overall": winrate(all_rows)}

        # По рынкам
        markets = {}
        for r in all_rows:
            m = r["market"]
            markets.setdefault(m, []).append(r)
        report["by_market"] = {
            m: {**winrate(rows), "avg_probability": round(sum(r["probability"] for r in rows) / len(rows), 3)}
            for m, rows in markets.items()
        }

        # По лигам
        comps = {}
        for r in all_rows:
            comps.setdefault(r["competition"], []).append(r)
        report["by_competition"] = {c_: winrate(rows) for c_, rows in comps.items()}

        # Калибровка по бакетам вероятности
        buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
        calibration = []
        for lo, hi in buckets:
            bucket_rows = [r for r in all_rows if lo <= r["probability"] < hi]
            if bucket_rows:
                wr = winrate(bucket_rows)
                calibration.append(
                    {
                        "bucket": f"{lo:.2f}-{hi:.2f}",
                        "predicted_avg": round(sum(r["probability"] for r in bucket_rows) / len(bucket_rows), 3),
                        "actual_winrate": wr["winrate"],
                        "count": wr["total"],
                    }
                )
        report["calibration"] = calibration

        # По прогрессу сезона
        stages = [
            ("начало (0-0.25)", 0, 0.25),
            ("середина (0.25-0.75)", 0.25, 0.75),
            ("конец (0.75-1.0)", 0.75, 1.01),
        ]
        report["by_season_progress"] = []
        for label, lo, hi in stages:
            rows = [r for r in all_rows if r["season_progress"] and lo <= r["season_progress"] < hi]
            if rows:
                report["by_season_progress"].append({"stage": label, **winrate(rows)})

        return report

    def get_recent_predictions(self, days: int = 7) -> List[dict]:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        c = self.conn.cursor()
        c.execute(
            """
            SELECT p.*, o.result
            FROM predictions p
            LEFT JOIN prediction_outcomes o ON p.id = o.prediction_id
            WHERE p.match_date >= ?
            ORDER BY p.match_date DESC
        """,
            (since,),
        )
        return [dict(r) for r in c.fetchall()]

