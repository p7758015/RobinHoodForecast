"""
Offline calibration reports for v2_predictions + match_results.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.offline.market_outcomes import v2_market_is_win
from football_agent.storage.v2_database import V2Database

PROB_BUCKETS = [
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.01),
]

ODDS_BANDS = [
    ("low (<1.35)", None, 1.35),
    ("medium (1.35-1.80)", 1.35, 1.80),
    ("high (>1.80)", 1.80, None),
]

SEASON_BUCKETS = [
    ("early (0-0.33)", 0.0, 0.33),
    ("mid (0.33-0.66)", 0.33, 0.66),
    ("late (0.66-1.0)", 0.66, 1.01),
]


def _winrate_stats(rows: List[dict]) -> dict:
    total = len(rows)
    if total == 0:
        return {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0, "avg_probability": 0.0}
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    return {
        "total": total,
        "wins": wins,
        "losses": total - wins,
        "winrate": round(wins / total, 4),
        "avg_probability": round(sum(r["probability"] for r in rows) / total, 4),
    }


def _rows_with_outcomes(db: Optional[V2Database] = None) -> List[dict]:
    db = db or V2Database()
    settled: List[dict] = []
    for row in db.fetch_settled_rows():
        outcome_bool = v2_market_is_win(row["market_key"], row["home_score"], row["away_score"])
        if outcome_bool is None:
            continue
        settled.append(
            {
                "match_date": row["match_date"],
                "competition": row["competition"],
                "market_key": row["market_key"],
                "probability": row["probability"],
                "book_odds": row["book_odds"],
                "season_progress": row["season_progress"],
                "outcome": "WIN" if outcome_bool else "LOSS",
            }
        )
    return settled


def get_v2_accuracy_report(db: Optional[V2Database] = None) -> dict:
    rows = _rows_with_outcomes(db)
    if not rows:
        return {"overall": _winrate_stats([]), "note": "no settled v2 rows"}
    return {"overall": _winrate_stats(rows), "settled_rows": len(rows)}


def get_v2_report_by_market(db: Optional[V2Database] = None) -> dict:
    rows = _rows_with_outcomes(db)
    by_market: Dict[str, List[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market_key"], []).append(r)
    return {
        market: {
            **_winrate_stats(mrows),
            "calibration_gap": round(
                _winrate_stats(mrows)["winrate"] - _winrate_stats(mrows)["avg_probability"],
                4,
            ),
        }
        for market, mrows in sorted(by_market.items())
    }


def get_v2_calibration_report(db: Optional[V2Database] = None) -> dict:
    rows = _rows_with_outcomes(db)
    if not rows:
        return {"probability_buckets": [], "by_odds_band": {}, "by_season_progress": []}

    prob_buckets = []
    for lo, hi in PROB_BUCKETS:
        bucket_rows = [r for r in rows if lo <= r["probability"] < hi]
        if bucket_rows:
            stats = _winrate_stats(bucket_rows)
            prob_buckets.append(
                {
                    "bucket": f"{lo:.2f}-{hi:.2f}",
                    "count": stats["total"],
                    "predicted_avg": stats["avg_probability"],
                    "actual_winrate": stats["winrate"],
                    "gap": round(stats["winrate"] - stats["avg_probability"], 4),
                }
            )

    by_odds: Dict[str, dict] = {}
    for label, lo, hi in ODDS_BANDS:
        if lo is None:
            band_rows = [r for r in rows if r["book_odds"] is not None and r["book_odds"] < hi]
        elif hi is None:
            band_rows = [r for r in rows if r["book_odds"] is not None and r["book_odds"] >= lo]
        else:
            band_rows = [
                r for r in rows if r["book_odds"] is not None and lo <= r["book_odds"] < hi
            ]
        if band_rows:
            by_odds[label] = _winrate_stats(band_rows)

    by_season = []
    for label, lo, hi in SEASON_BUCKETS:
        srows = [
            r
            for r in rows
            if r["season_progress"] is not None and lo <= float(r["season_progress"]) < hi
        ]
        if srows:
            by_season.append({"stage": label, **_winrate_stats(srows)})

    by_comp: Dict[str, List[dict]] = {}
    for r in rows:
        by_comp.setdefault(r["competition"], []).append(r)

    return {
        "probability_buckets": prob_buckets,
        "by_odds_band": by_odds,
        "by_season_progress": by_season,
        "by_competition": {k: _winrate_stats(v) for k, v in sorted(by_comp.items())},
    }


def build_full_v2_report(db: Optional[V2Database] = None) -> dict:
    db = db or V2Database()
    return {
        "pipeline_version": "v2",
        "stored_predictions": db.count_predictions(),
        "accuracy": get_v2_accuracy_report(db),
        "by_market": get_v2_report_by_market(db),
        "calibration": get_v2_calibration_report(db),
    }
