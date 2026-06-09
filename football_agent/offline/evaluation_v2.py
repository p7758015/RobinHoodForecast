"""
Offline evaluation over persisted v2 analysis artifacts.

No future leakage:
- Uses ONLY persisted run artifacts + final match_results.
- Does NOT re-run ingestion, merge, builder, or scorer.

Primary evaluation unit: best_market outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from football_agent.storage.match_key import normalize_team_for_key


CALIBRATION_BUCKETS: Tuple[Tuple[float, float], ...] = (
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),
)


@dataclass(frozen=True)
class Settlement:
    resolved: bool
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    match_date: Optional[str] = None
    join_method: Optional[str] = None  # exact | normalized | unresolved


def resolve_match_result(
    *,
    match_date: str,
    home_team: str,
    away_team: str,
    exact_lookup,
    date_lookup,
) -> Settlement:
    """
    Deterministic, fail-soft join:
    1) exact match_results lookup
    2) normalized match on same date (unique only)
    """
    exact = exact_lookup(match_date, home_team, away_team)
    if exact:
        return Settlement(
            resolved=True,
            home_score=int(exact["home_score"]),
            away_score=int(exact["away_score"]),
            match_date=match_date,
            join_method="exact",
        )

    candidates = date_lookup(match_date) or []
    th = normalize_team_for_key(home_team)
    ta = normalize_team_for_key(away_team)
    hits: List[dict] = []
    for r in candidates:
        if normalize_team_for_key(r.get("home_team", "")) == th and normalize_team_for_key(r.get("away_team", "")) == ta:
            hits.append(r)
    if len(hits) == 1:
        r = hits[0]
        return Settlement(
            resolved=True,
            home_score=int(r["home_score"]),
            away_score=int(r["away_score"]),
            match_date=match_date,
            join_method="normalized",
        )

    return Settlement(resolved=False, match_date=match_date, join_method="unresolved")


def settle_best_market(market_key: str, hs: int, as_: int) -> Optional[bool]:
    """Return True/False if market is settlement-compatible; else None."""
    if market_key == "HOME_WIN":
        return hs > as_
    if market_key == "AWAY_WIN":
        return as_ > hs
    if market_key == "HOME_NOT_LOSE":
        return hs >= as_
    if market_key == "AWAY_NOT_LOSE":
        return as_ >= hs
    if market_key == "BTTS_YES":
        return hs >= 1 and as_ >= 1
    if market_key == "HOME_TEAM_TO_SCORE":
        return hs >= 1
    if market_key == "AWAY_TEAM_TO_SCORE":
        return as_ >= 1
    if market_key == "OVER_1_5":
        return (hs + as_) >= 2
    return None


def bucket_index(p: float) -> Optional[int]:
    for i, (lo, hi) in enumerate(CALIBRATION_BUCKETS):
        if lo <= p < hi or (i == len(CALIBRATION_BUCKETS) - 1 and lo <= p <= hi):
            return i
    return None


def evaluate_best_market_runs(
    runs: List[dict],
    *,
    exact_lookup,
    date_lookup,
) -> Dict[str, Any]:
    """
    runs: list of dicts with at least:
      - snapshot_meta: match_date_utc + home_team.name + away_team.name
      - prediction: best_market{market_key, probability, book_odds}
      - report fields (optional) for slicing done outside this core fn
    """
    total_scored = len(runs)
    settled = 0
    best_present = 0
    book_odds_present = 0
    settlement_compatible = 0

    wins = 0
    roi_subset = 0
    roi_total_profit = 0.0

    # calibration accumulators
    buckets = [
        {"range": f"[{lo:.2f},{hi:.2f})" if hi < 1.01 else f"[{lo:.2f},{hi:.2f}]", "count": 0, "predicted_avg": 0.0, "wins": 0}
        for (lo, hi) in CALIBRATION_BUCKETS
    ]

    for item in runs:
        match_date = item["match_date"]
        home = item["home_team"]
        away = item["away_team"]
        best = item.get("best_market")

        st = resolve_match_result(
            match_date=match_date,
            home_team=home,
            away_team=away,
            exact_lookup=exact_lookup,
            date_lookup=date_lookup,
        )
        if not st.resolved:
            continue
        settled += 1

        if not best:
            continue
        best_present += 1

        mk = best.get("market_key")
        p = best.get("probability")
        bo = best.get("book_odds")
        if isinstance(bo, (int, float)) and bo is not None:
            book_odds_present += 1

        if not isinstance(mk, str) or not isinstance(p, (int, float)):
            continue

        outcome = settle_best_market(mk, int(st.home_score), int(st.away_score))  # type: ignore[arg-type]
        if outcome is None:
            continue
        settlement_compatible += 1

        if outcome:
            wins += 1

        bi = bucket_index(float(p))
        if bi is not None:
            buckets[bi]["count"] += 1
            buckets[bi]["predicted_avg"] += float(p)
            buckets[bi]["wins"] += 1 if outcome else 0

        # ROI-like subset: strict constraints
        if isinstance(bo, (int, float)) and bo is not None and bo > 1.0:
            roi_subset += 1
            roi_total_profit += (float(bo) - 1.0) if outcome else -1.0

    # finalize calibration
    for b in buckets:
        if b["count"] > 0:
            b["predicted_avg"] = round(b["predicted_avg"] / b["count"], 4)
            b["actual_winrate"] = round(b["wins"] / b["count"], 4)
        else:
            b["predicted_avg"] = None
            b["actual_winrate"] = None

    report: Dict[str, Any] = {
        "counts": {
            "scored_runs": total_scored,
            "settled_runs": settled,
            "best_market_present": best_present,
            "best_market_book_odds_present": book_odds_present,
            "best_market_settlement_compatible": settlement_compatible,
            "roi_subset": roi_subset,
        },
        "metrics": {
            "settled_coverage": round(settled / total_scored, 4) if total_scored else 0.0,
            "best_market_hit_rate": round(wins / settlement_compatible, 4) if settlement_compatible else None,
            "roi_total_profit": round(roi_total_profit, 4) if roi_subset else None,
            "roi_mean_profit": round(roi_total_profit / roi_subset, 4) if roi_subset else None,
            "roi": round(roi_total_profit / roi_subset, 4) if roi_subset else None,  # stake=1 per bet
        },
        "calibration": {"buckets": buckets},
    }
    return report

