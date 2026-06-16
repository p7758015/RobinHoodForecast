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

# Canonical settlement identity contract (prediction/run side → match_results).
#
# Prediction identity fields (all required for evaluable runs):
#   - match_date: YYYY-MM-DD UTC kickoff date
#   - home_team: display name string
#   - away_team: display name string
#
# Sources (priority):
#   1. persisted snapshot ``match_meta`` (primary)
#   2. ``analysis_runs_v2`` header columns home_team / away_team / kickoff_utc (fallback)
#
# Storage side (unchanged schema):
#   match_results(match_date, home_team, away_team, home_score, away_score, ...)
#   as written by V2Database.save_match_result / batch-persist.
#
# Join algorithm (same match_date only):
#   1. exact SQL lookup on raw (match_date, home_team, away_team) → join_method=exact
#   2. else normalized scan via date_lookup + normalize_team_for_settlement
#      - 0 hits → unresolved
#      - 2+ hits → unresolved (ambiguous; never pick arbitrarily)
#      - 1 hit → join_method=normalized
#
# match_key is NOT a settlement join key in this layer.
SETTLEMENT_IDENTITY_CONTRACT = (
    "match_date:YYYY-MM-DD UTC; home_team; away_team → match_results; "
    "exact primary, normalized fallback, ambiguous unresolved"
)


CALIBRATION_BUCKETS: Tuple[Tuple[float, float], ...] = (
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),
)

# Fail-soft bucket labels for diagnostic report slices (persisted artifacts only).
SLICE_UNKNOWN = "unknown"
SLICE_NONE = "(none)"
SLICE_REPORT_MISSING = "report_missing"


@dataclass(frozen=True)
class Settlement:
    resolved: bool
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    match_date: Optional[str] = None
    join_method: Optional[str] = None  # exact | normalized | unresolved


@dataclass(frozen=True)
class SettlementIdentity:
    """Evaluable run identity for joining to match_results."""

    match_date: str
    home_team: str
    away_team: str
    source: str  # snapshot_meta | run_header | mixed


def normalize_team_for_settlement(name: str) -> str:
    """
    Team name normalization for settlement join.

    Uses the same rules as ``storage.match_key.normalize_team_for_key``
    (lowercase, collapse spaces, non-alnum → underscore).
    """
    return normalize_team_for_key(name)


def _strip_team(value: str) -> str:
    return (value or "").strip()


def _match_date_from_iso(value: Optional[str]) -> Optional[str]:
    if isinstance(value, str) and len(value.strip()) >= 10:
        return value.strip()[:10]
    return None


def _team_name_from_meta_field(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_settlement_identity(
    *,
    snapshot_json: Optional[dict],
    run_home_team: Optional[str] = None,
    run_away_team: Optional[str] = None,
    run_kickoff_utc: Optional[str] = None,
) -> Optional[SettlementIdentity]:
    """
    Build evaluable settlement identity from persisted run artifacts.

    See ``SETTLEMENT_IDENTITY_CONTRACT`` for field definitions and source priority.
    Returns ``None`` when match_date or either team name cannot be resolved.
    """
    meta = {}
    if isinstance(snapshot_json, dict):
        raw_meta = snapshot_json.get("match_meta")
        if isinstance(raw_meta, dict):
            meta = raw_meta

    snap_date = _match_date_from_iso(meta.get("match_date_utc"))
    snap_home = _team_name_from_meta_field(meta.get("home_team"))
    snap_away = _team_name_from_meta_field(meta.get("away_team"))

    header_date = _match_date_from_iso(run_kickoff_utc)
    header_home = _strip_team(run_home_team) if isinstance(run_home_team, str) else None
    header_away = _strip_team(run_away_team) if isinstance(run_away_team, str) else None
    if not header_home:
        header_home = None
    if not header_away:
        header_away = None

    match_date = snap_date or header_date
    home_team = snap_home or header_home
    away_team = snap_away or header_away

    if not match_date or not home_team or not away_team:
        return None

    used_snap = bool(snap_date and snap_home and snap_away)
    used_header = bool(
        (not snap_date and header_date)
        or (not snap_home and header_home)
        or (not snap_away and header_away)
    )
    if used_snap and used_header:
        source = "mixed"
    elif used_snap:
        source = "snapshot_meta"
    else:
        source = "run_header"

    return SettlementIdentity(
        match_date=match_date,
        home_team=home_team,
        away_team=away_team,
        source=source,
    )


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

    See ``SETTLEMENT_IDENTITY_CONTRACT`` for the canonical identity and join rules.
    """
    date_str = (match_date or "").strip()
    pred_home = _strip_team(home_team)
    pred_away = _strip_team(away_team)
    th = normalize_team_for_settlement(pred_home)
    ta = normalize_team_for_settlement(pred_away)

    exact = exact_lookup(date_str, pred_home, pred_away)
    if exact:
        return Settlement(
            resolved=True,
            home_score=int(exact["home_score"]),
            away_score=int(exact["away_score"]),
            match_date=date_str,
            join_method="exact",
        )

    candidates = date_lookup(date_str) or []
    hits: List[dict] = []
    for row in candidates:
        row_home = normalize_team_for_settlement(str(row.get("home_team") or ""))
        row_away = normalize_team_for_settlement(str(row.get("away_team") or ""))
        if row_home == th and row_away == ta:
            hits.append(row)

    if len(hits) == 0:
        return Settlement(resolved=False, match_date=date_str, join_method="unresolved")

    if len(hits) > 1:
        return Settlement(resolved=False, match_date=date_str, join_method="unresolved")

    row = hits[0]
    return Settlement(
        resolved=True,
        home_score=int(row["home_score"]),
        away_score=int(row["away_score"]),
        match_date=date_str,
        join_method="normalized",
    )


def settle_best_market(market_key: str, hs: int, as_: int) -> Optional[bool]:
    """Run-level evaluation alias for canonical :func:`evaluate_market_outcome`."""
    return evaluate_market_outcome(market_key, hs, as_)


def bucket_index(p: float) -> Optional[int]:
    for i, (lo, hi) in enumerate(CALIBRATION_BUCKETS):
        if lo <= p < hi or (i == len(CALIBRATION_BUCKETS) - 1 and lo <= p <= hi):
            return i
    return None


def _bump_counter(counter: Dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _as_warning_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if x is not None and str(x).strip()]


def _missing_blocks_signature(blocks: List[str]) -> str:
    if not blocks:
        return SLICE_NONE
    return "+".join(sorted(set(blocks)))


def _link_strategy_from_report(report: Optional[dict], field: str) -> str:
    if not isinstance(report, dict):
        return SLICE_REPORT_MISSING
    value = report.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return SLICE_UNKNOWN


def _competition_code_label(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return SLICE_UNKNOWN


def _competition_name_label(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return SLICE_UNKNOWN


def build_evaluation_report_slices(
    runs: List[dict],
    *,
    evaluable_runs_total: int,
    scored_runs_total: int,
) -> Dict[str, Any]:
    """
    Diagnostic breakdowns from persisted run artifacts only.

    Sources per evaluable run:
    - ``report`` (analysis_build_reports_v2.report_json): merge_missing_blocks,
      openclaw_link_strategy, odds_link_strategy, merge_warnings, builder_warnings
    - ``scoring_warnings`` (analysis_predictions_v2.scoring_warnings_json)
    - ``competition_code`` (analysis_runs_v2.competition_code)
    - ``competition_name`` (snapshot match_meta.competition_name when present)
    """
    by_missing_signature: Dict[str, int] = {}
    by_missing_block: Dict[str, int] = {}
    by_openclaw_link: Dict[str, int] = {}
    by_odds_link: Dict[str, int] = {}
    by_competition_code: Dict[str, int] = {}
    by_competition_name: Dict[str, int] = {}

    runs_with_report = 0
    runs_without_report = 0
    runs_with_any_warning = 0
    runs_without_warnings = 0
    warnings_by_kind = {
        "merge_warnings": 0,
        "builder_warnings": 0,
        "scoring_warnings": 0,
    }

    for item in runs:
        report = item.get("report")
        has_report = isinstance(report, dict)
        if has_report:
            runs_with_report += 1
        else:
            runs_without_report += 1

        if has_report:
            blocks = _as_warning_list(report.get("merge_missing_blocks"))
            signature = _missing_blocks_signature(blocks)
            for block in set(blocks):
                _bump_counter(by_missing_block, block)
        else:
            signature = SLICE_REPORT_MISSING

        _bump_counter(by_missing_signature, signature)
        _bump_counter(by_openclaw_link, _link_strategy_from_report(report if has_report else None, "openclaw_link_strategy"))
        _bump_counter(by_odds_link, _link_strategy_from_report(report if has_report else None, "odds_link_strategy"))

        merge_warnings = _as_warning_list(report.get("merge_warnings")) if has_report else []
        builder_warnings = _as_warning_list(report.get("builder_warnings")) if has_report else []
        scoring_warnings = _as_warning_list(item.get("scoring_warnings"))

        if merge_warnings:
            warnings_by_kind["merge_warnings"] += 1
        if builder_warnings:
            warnings_by_kind["builder_warnings"] += 1
        if scoring_warnings:
            warnings_by_kind["scoring_warnings"] += 1

        if merge_warnings or builder_warnings or scoring_warnings:
            runs_with_any_warning += 1
        else:
            runs_without_warnings += 1

        _bump_counter(by_competition_code, _competition_code_label(item.get("competition_code")))
        _bump_counter(by_competition_name, _competition_name_label(item.get("competition_name")))

    return {
        "meta": {
            "evaluable_runs_total": evaluable_runs_total,
            "scored_runs_total": scored_runs_total,
            "runs_with_report": runs_with_report,
            "runs_without_report": runs_without_report,
        },
        "missing_blocks": {
            "by_signature": by_missing_signature,
            "by_block": by_missing_block,
        },
        "openclaw_link_strategy": by_openclaw_link,
        "odds_link_strategy": by_odds_link,
        "warnings": {
            "runs_with_any_warning": runs_with_any_warning,
            "runs_without_warnings": runs_without_warnings,
            "by_kind": warnings_by_kind,
        },
        "competition_code": by_competition_code,
        "competition_name": by_competition_name,
    }


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
        "slices": build_evaluation_report_slices(
            runs,
            evaluable_runs_total=evaluable_runs_total,
            scored_runs_total=scored_runs_total,
        ),
    }
    return report

