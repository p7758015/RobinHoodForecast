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

from football_agent.offline.market_outcomes import evaluate_market_outcome
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


def normalize_team_for_settlement(name: str) -> str:
    """
    Team name normalization for settlement join.

    Uses the same rules as ``storage.match_key.normalize_team_for_key``
    (lowercase, collapse spaces, non-alnum → underscore).
    """
    return normalize_team_for_key(name)


def _strip_team(value: str) -> str:
    return (value or "").strip()


def resolve_match_result(
    *,
    match_date: str,
    home_team: str,
    away_team: str,
    exact_lookup,
    date_lookup,
) -> Settlement:
    """
    Join a scored run identity to a persisted ``match_results`` row.

    Settlement identity contract (prediction side)
    --------------------------------------------
    Fields taken from persisted snapshot ``match_meta`` (via offline service):
    - ``match_date``: YYYY-MM-DD (UTC kickoff date)
    - ``home_team``: display name string
    - ``away_team``: display name string

    Storage side (unchanged): ``match_results(match_date, home_team, away_team, ...)``
    as written by callers of ``V2Database.save_match_result``.

    Join algorithm (same ``match_date`` only)
    -----------------------------------------
    1. Load all ``match_results`` rows for ``match_date`` via ``date_lookup``.
    2. Keep rows whose normalized home/away equal the prediction identity
       (``normalize_team_for_settlement``).
    3. Resolution:
       - **0 hits** → ``unresolved`` (no result for this identity on the date).
       - **2+ hits** → ``unresolved`` (ambiguous normalized match on the date).
       - **1 hit**:
         - raw ``home_team``/``away_team`` equal prediction strings (strip) → **exact**
         - otherwise → **normalized** (same normalized identity, different raw spelling)

    ``exact_lookup`` is retained for API compatibility; resolution is driven by
    the normalized scan above. A direct SQL exact hit with no normalized candidate
    still resolves as **exact** when ``exact_lookup`` returns a row.

    ``match_key`` is not used as a join key in this layer.
    """
    date_str = (match_date or "").strip()
    pred_home = _strip_team(home_team)
    pred_away = _strip_team(away_team)
    th = normalize_team_for_settlement(pred_home)
    ta = normalize_team_for_settlement(pred_away)

    candidates = date_lookup(date_str) or []
    hits: List[dict] = []
    for row in candidates:
        row_home = normalize_team_for_settlement(str(row.get("home_team") or ""))
        row_away = normalize_team_for_settlement(str(row.get("away_team") or ""))
        if row_home == th and row_away == ta:
            hits.append(row)

    if len(hits) == 0:
        # Fast path: legacy exact SQL key (raw strings) when normalized scan found nothing.
        exact = exact_lookup(date_str, pred_home, pred_away)
        if exact:
            return Settlement(
                resolved=True,
                home_score=int(exact["home_score"]),
                away_score=int(exact["away_score"]),
                match_date=date_str,
                join_method="exact",
            )
        return Settlement(resolved=False, match_date=date_str, join_method="unresolved")

    if len(hits) > 1:
        return Settlement(resolved=False, match_date=date_str, join_method="unresolved")

    row = hits[0]
    raw_exact = (
        _strip_team(str(row.get("home_team") or "")) == pred_home
        and _strip_team(str(row.get("away_team") or "")) == pred_away
    )
    return Settlement(
        resolved=True,
        home_score=int(row["home_score"]),
        away_score=int(row["away_score"]),
        match_date=date_str,
        join_method="exact" if raw_exact else "normalized",
    )


def settle_best_market(market_key: str, hs: int, as_: int) -> Optional[bool]:
    """Run-level evaluation alias for canonical :func:`evaluate_market_outcome`."""
    return evaluate_market_outcome(market_key, hs, as_)


def bucket_index(p: float) -> Optional[int]:
    for i, (lo, hi) in enumerate(CALIBRATION_BUCKETS):
        if lo <= p < hi or (i == len(CALIBRATION_BUCKETS) - 1 and lo <= p <= hi):
            return i
    return None


def evaluate_best_market_runs(
    runs: List[dict],
    *,
    scored_runs_total: int,
    exact_lookup,
    date_lookup,
) -> Dict[str, Any]:
    """
    Evaluate evaluable runs (identity present) against persisted match_results.

    runs: list of dicts with at least:
      - match_date, home_team, away_team
      - best_market{market_key, probability, book_odds} (optional)
      - report fields (optional) for slicing done outside this core fn

    scored_runs_total: all scored runs from storage before identity filtering.
    """
    evaluable_runs_total = len(runs)
    skipped_identity_runs_total = max(0, scored_runs_total - evaluable_runs_total)

    settled_runs_total = 0
    join_exact_count = 0
    join_normalized_count = 0
    join_unresolved_count = 0

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

        bo = best.get("book_odds") if isinstance(best, dict) else None
        if isinstance(bo, (int, float)) and bo is not None:
            book_odds_present += 1

        st = resolve_match_result(
            match_date=match_date,
            home_team=home,
            away_team=away,
            exact_lookup=exact_lookup,
            date_lookup=date_lookup,
        )
        if st.join_method == "exact":
            join_exact_count += 1
        elif st.join_method == "normalized":
            join_normalized_count += 1
        else:
            join_unresolved_count += 1

        if not st.resolved:
            continue
        settled_runs_total += 1

        if not best:
            continue
        best_present += 1

        mk = best.get("market_key")
        p = best.get("probability")

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

        # ROI-like subset: strict constraints (settled runs with book_odds > 1.0)
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
            "scored_runs_total": scored_runs_total,
            "evaluable_runs_total": evaluable_runs_total,
            "settled_runs_total": settled_runs_total,
            "skipped_identity_runs_total": skipped_identity_runs_total,
            "join_exact_count": join_exact_count,
            "join_normalized_count": join_normalized_count,
            "join_unresolved_count": join_unresolved_count,
            # legacy aliases (CLI / older tests)
            "scored_runs": scored_runs_total,
            "settled_runs": settled_runs_total,
            "best_market_present": best_present,
            "best_market_book_odds_present": book_odds_present,
            "best_market_settlement_compatible": settlement_compatible,
            "roi_subset": roi_subset,
        },
        "metrics": {
            "settled_coverage": (
                round(settled_runs_total / evaluable_runs_total, 4) if evaluable_runs_total else 0.0
            ),
            "evaluable_coverage": (
                round(evaluable_runs_total / scored_runs_total, 4) if scored_runs_total else 0.0
            ),
            "odds_coverage": (
                round(book_odds_present / evaluable_runs_total, 4) if evaluable_runs_total else 0.0
            ),
            "best_market_hit_rate": round(wins / settlement_compatible, 4) if settlement_compatible else None,
            "roi_total_profit": round(roi_total_profit, 4) if roi_subset else None,
            "roi_mean_profit": round(roi_total_profit / roi_subset, 4) if roi_subset else None,
            "roi": round(roi_total_profit / roi_subset, 4) if roi_subset else None,  # stake=1 per bet
        },
        "calibration": {"buckets": buckets},
    }
    return report

