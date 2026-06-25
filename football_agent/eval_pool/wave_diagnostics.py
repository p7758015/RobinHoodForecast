"""
Read-only settlement diagnostics and quality snapshots for eval waves.

Does not modify predictions, snapshots, or reports — only reads persisted artifacts
and optionally probes external fixture fetch (no DB writes on probe).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from football_agent.eval_pool.calibration_report import (
    POOL_CONFIDENCE_BUCKETS,
    SettledPoolEvalRecord,
    build_pool_calibration_review,
    collect_settled_pool_eval_records,
    pool_confidence_bucket_label,
)
from football_agent.eval_pool.result_fetch_probe import probe_wave_result_fetch
from football_agent.eval_pool.report import _in_pool_scope, _snapshot_meta
from football_agent.eval_pool.scope import LOW_CONFIDENCE_THRESHOLD, filter_pool_keys, resolve_pool_entry
from football_agent.eval_pool.settle import is_finished_status
from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.eval_pool.wave_predictions import WavePredictionView, collect_wave_predictions
from football_agent.offline.evaluation_v2 import (
    extract_settlement_identity,
    normalize_team_for_settlement,
    resolve_match_result,
)
from football_agent.paths import DEFAULT_DB_PATH, EVAL_WAVE_REPORTS_DIR, ensure_runtime_dirs
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2, EvaluationRunRow

# Extended confidence buckets for wave quality (includes low-confidence slice).
WAVE_CONFIDENCE_BUCKETS: Tuple[Tuple[float, float, str], ...] = (
    (0.40, 0.50, "0.40-0.49"),
    *POOL_CONFIDENCE_BUCKETS,
)

REASON_NO_FIXTURES_IN_SCOPE = "no_fixtures_in_scope"
REASON_FIXTURE_OUT_OF_RANGE = "fixture_out_of_range_after_filter"
REASON_MISSING_FIXTURE_DATE = "missing_fixture_date"
REASON_RESULT_NOT_FINISHED = "result_not_finished"
REASON_RESULT_NOT_SAVED = "result_not_saved_to_db"
REASON_NO_CANDIDATES = "no_match_result_candidates"
REASON_JOIN_MISS = "join_exact_miss_normalized_miss"
REASON_COMPETITION_MISMATCH = "competition_mismatch"
REASON_TEAM_MISMATCH = "team_name_mismatch"
REASON_MISSING_IDENTITY = "missing_identity_fields"
REASON_UNEXPECTED = "unexpected_exception"
REASON_SETTLED = "settled"


def wave_confidence_bucket_label(confidence: Optional[float]) -> Optional[str]:
    if confidence is None:
        return None
    for lo, hi, label in WAVE_CONFIDENCE_BUCKETS:
        if lo <= confidence < hi or (label == "0.80+" and lo <= confidence <= hi):
            return label
    return None


@dataclass(frozen=True)
class RunSettlementDiagnostic:
    run_id: str
    date: str
    pool_key: str
    home: str
    away: str
    best_market: Optional[str]
    confidence: Optional[float]
    expected_match_date: Optional[str]
    candidate_fixtures_found: int
    candidate_result_rows: int
    join_status: str
    unresolved_reason: str
    detail: str


def _count_match_results_in_range(repo: EvaluationRepositoryV2, date_from: str, date_to: str) -> int:
    cur = repo.conn.execute(
        """
        SELECT COUNT(*) FROM match_results
         WHERE match_date >= ? AND match_date <= ?
        """,
        (date_from, date_to),
    )
    return int(cur.fetchone()[0])


def _probe_pool_fixtures_in_range(
    manifest: EvalWaveManifest,
    *,
    scraper_url: Optional[str] = None,
) -> Dict[str, Dict[str, int]]:
    """Read-only probe via result_fetch_probe (replaces ad-hoc discovery scan)."""
    payload = probe_wave_result_fetch(
        league_keys=list(manifest.league_keys),
        date_from=manifest.date_from,
        date_to=manifest.date_to,
        scraper_url=scraper_url,
        detail_sample_limit=2,
    )
    pools: Dict[str, Dict[str, int]] = {}
    for p in payload.get("pools") or []:
        pools[str(p.get("pool_key"))] = {
            "fixtures_returned": int(p.get("fixtures_returned") or 0),
            "fixtures_in_range": int(p.get("in_range_count") or 0),
            "fixtures_finished_in_list": int(p.get("finished_list_count") or 0),
            "detail_probes_attempted": int(p.get("detail_probes_attempted") or 0),
            "detail_finished_confirmed": int(p.get("detail_finished_confirmed") or 0),
            "primary_blocker": p.get("primary_blocker"),
        }
    return pools


def _diagnose_run(
    row: EvaluationRunRow,
    view: WavePredictionView,
    *,
    repo: EvaluationRepositoryV2,
    allowed_keys: Sequence[str],
    pool_probe: Dict[str, Dict[str, int]],
    match_results_in_wave: int,
) -> RunSettlementDiagnostic:
    snap = row.snapshot_json or {}
    pred = row.prediction_json if isinstance(row.prediction_json, dict) else {}
    best = pred.get("best_market") if isinstance(pred.get("best_market"), dict) else {}
    best_market = str(best.get("market_key")) if best.get("market_key") else None
    confidence = float(pred["overall_confidence_score"]) if isinstance(pred.get("overall_confidence_score"), (int, float)) else view.confidence

    if view.settle_status == "settled":
        return RunSettlementDiagnostic(
            run_id=view.run_id,
            date=view.date,
            pool_key=view.pool_key,
            home=view.home_team,
            away=view.away_team,
            best_market=best_market,
            confidence=confidence,
            expected_match_date=view.date,
            candidate_fixtures_found=pool_probe.get(view.pool_key, {}).get("fixtures_in_range", 0),
            candidate_result_rows=0,
            join_status="settled",
            unresolved_reason=REASON_SETTLED,
            detail=view.final_score or "",
        )

    if view.settle_status == "parked":
        return RunSettlementDiagnostic(
            run_id=view.run_id,
            date=view.date,
            pool_key=view.pool_key,
            home=view.home_team,
            away=view.away_team,
            best_market=best_market,
            confidence=confidence,
            expected_match_date=view.date,
            candidate_fixtures_found=0,
            candidate_result_rows=0,
            join_status="parked",
            unresolved_reason="parked_analysis_only",
            detail="analysis_only run — excluded from settlement",
        )

    meta = _snapshot_meta(snap)
    comp_name = meta.get("competition_name") or row.competition_code
    comp_country = meta.get("country")
    entry = resolve_pool_entry(
        str(comp_name) if comp_name else None,
        str(comp_country) if comp_country else None,
    )
    if entry is None or entry.key not in allowed_keys:
        return RunSettlementDiagnostic(
            run_id=view.run_id,
            date=view.date,
            pool_key=view.pool_key,
            home=view.home_team,
            away=view.away_team,
            best_market=best_market,
            confidence=confidence,
            expected_match_date=view.date,
            candidate_fixtures_found=0,
            candidate_result_rows=0,
            join_status="unresolved",
            unresolved_reason=REASON_COMPETITION_MISMATCH,
            detail=f"competition not in wave pool: {comp_name}",
        )

    identity = extract_settlement_identity(
        snapshot_json=snap,
        run_home_team=row.home_team,
        run_away_team=row.away_team,
        run_kickoff_utc=row.kickoff_utc,
    )
    if identity is None:
        return RunSettlementDiagnostic(
            run_id=view.run_id,
            date=view.date,
            pool_key=view.pool_key,
            home=view.home_team,
            away=view.away_team,
            best_market=best_market,
            confidence=confidence,
            expected_match_date=view.date,
            candidate_fixtures_found=pool_probe.get(view.pool_key, {}).get("fixtures_in_range", 0),
            candidate_result_rows=0,
            join_status="unresolved",
            unresolved_reason=REASON_MISSING_IDENTITY,
            detail="cannot resolve match_date/home/away from snapshot or run header",
        )

    match_date = identity.match_date
    candidates = repo.fetch_match_results_for_date(match_date)
    candidate_count = len(candidates)

    pool_in_range = pool_probe.get(view.pool_key, {}).get("fixtures_in_range", 0)
    pool_finished = pool_probe.get(view.pool_key, {}).get("fixtures_finished_in_list", 0)

    settlement = resolve_match_result(
        match_date=match_date,
        home_team=identity.home_team,
        away_team=identity.away_team,
        exact_lookup=repo.fetch_match_result_exact,
        date_lookup=repo.fetch_match_results_for_date,
    )
    if settlement.resolved:
        return RunSettlementDiagnostic(
            run_id=view.run_id,
            date=view.date,
            pool_key=view.pool_key,
            home=view.home_team,
            away=view.away_team,
            best_market=best_market,
            confidence=confidence,
            expected_match_date=match_date,
            candidate_fixtures_found=pool_in_range,
            candidate_result_rows=candidate_count,
            join_status="settled",
            unresolved_reason=REASON_SETTLED,
            detail=f"join={settlement.join_method}",
        )

    reason = REASON_JOIN_MISS
    detail = "no exact or normalized join to match_results"

    if match_results_in_wave == 0:
        if pool_in_range == 0:
            reason = REASON_NO_FIXTURES_IN_SCOPE
            detail = (
                "result fetch found 0 in-range fixtures for pool; "
                "match_results table empty for wave — likely discovery/daily-list path mismatch"
            )
        elif pool_finished == 0 and pool_in_range > 0:
            reason = REASON_RESULT_NOT_FINISHED
            detail = (
                f"discovery sees {pool_in_range} in-range fixtures but none finished in list; "
                "match_results not populated"
            )
        else:
            reason = REASON_RESULT_NOT_SAVED
            detail = "fixtures may exist externally but no rows saved to match_results for wave"
    elif candidate_count == 0:
        reason = REASON_NO_CANDIDATES
        detail = f"no match_results rows for date {match_date}"
    else:
        th = normalize_team_for_settlement(identity.home_team)
        ta = normalize_team_for_settlement(identity.away_team)
        partial = [
            r
            for r in candidates
            if normalize_team_for_settlement(str(r.get("home_team") or "")) == th
            or normalize_team_for_settlement(str(r.get("away_team") or "")) == ta
        ]
        if partial:
            reason = REASON_TEAM_MISMATCH
            detail = f"{candidate_count} result row(s) on date but team names do not match after normalization"
        else:
            reason = REASON_JOIN_MISS
            detail = f"{candidate_count} result row(s) on date; identity teams not among candidates"

    return RunSettlementDiagnostic(
        run_id=view.run_id,
        date=view.date,
        pool_key=view.pool_key,
        home=view.home_team,
        away=view.away_team,
        best_market=best_market,
        confidence=confidence,
        expected_match_date=match_date,
        candidate_fixtures_found=pool_in_range,
        candidate_result_rows=candidate_count,
        join_status="unresolved",
        unresolved_reason=reason,
        detail=detail,
    )


def build_wave_settlement_diagnostics(
    manifest: EvalWaveManifest,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    probe_fetch: bool = True,
    scraper_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only diagnostics for wave settlement blockers."""
    views = collect_wave_predictions(manifest, db_path=db_path)
    repo = EvaluationRepositoryV2(db_path=db_path)
    try:
        match_results_in_wave = _count_match_results_in_range(
            repo, manifest.date_from, manifest.date_to
        )
        pool_probe = _probe_pool_fixtures_in_range(manifest, scraper_url=scraper_url) if probe_fetch else {}
        result_source = (
            probe_wave_result_fetch(
                league_keys=list(manifest.league_keys),
                date_from=manifest.date_from,
                date_to=manifest.date_to,
                scraper_url=scraper_url,
                detail_sample_limit=2,
            )
            if probe_fetch
            else None
        )

        view_by_id = {v.run_id: v for v in views}
        rows = list(
            repo.iter_scored_runs(
                date_from=manifest.date_from,
                date_to=f"{manifest.date_to}T23:59:59",
                limit=50000,
            )
        )

        per_run: List[RunSettlementDiagnostic] = []
        for row in rows:
            snap = row.snapshot_json or {}
            meta = _snapshot_meta(snap)
            comp_name = meta.get("competition_name") or row.competition_code
            comp_country = meta.get("country")
            if not _in_pool_scope(
                competition_name=str(comp_name) if comp_name else None,
                competition_country=str(comp_country) if comp_country else None,
                allowed_keys=tuple(manifest.league_keys),
            ):
                continue
            view = view_by_id.get(row.run_id)
            if view is None:
                continue
            per_run.append(
                _diagnose_run(
                    row,
                    view,
                    repo=repo,
                    allowed_keys=tuple(manifest.league_keys),
                    pool_probe=pool_probe,
                    match_results_in_wave=match_results_in_wave,
                )
            )

        reason_buckets = Counter(d.unresolved_reason for d in per_run if d.join_status != "settled")
        settled = [d for d in per_run if d.join_status == "settled"]
        unresolved = [d for d in per_run if d.join_status == "unresolved"]
        pending_views = [v for v in views if v.settle_status == "pending"]

        missing_odds = sum(
            1 for v in views if v.best_market_odds is None and v.settle_status != "parked"
        )
        weak_identity = sum(1 for d in per_run if d.unresolved_reason == REASON_MISSING_IDENTITY)
        ambiguous_comp = sum(1 for d in per_run if d.unresolved_reason == REASON_COMPETITION_MISMATCH)

        records, collection_stats = collect_settled_pool_eval_records(
            rows,
            allowed_keys=tuple(manifest.league_keys),
            repo=repo,
        )

        total_saved = len(views)
        unresolved_n = len(unresolved)
        return {
            "pipeline": "wave_settlement_diagnostics",
            "wave_name": manifest.wave_name,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_saved_runs": total_saved,
                "total_runs_in_pool": total_saved,
                "match_results_rows_in_wave_dates": match_results_in_wave,
                "finished_fixtures_probed": sum(
                    p.get("fixtures_finished_in_list", 0) for p in pool_probe.values()
                ),
                "fixtures_in_range_probed": sum(
                    p.get("fixtures_in_range", 0) for p in pool_probe.values()
                ),
                "settled_evaluable": len(records),
                "unresolved_count": unresolved_n,
                "unresolved_share": round(unresolved_n / total_saved, 4) if total_saved else 0.0,
                "join_exact_count": collection_stats.get("join_exact_count", 0),
                "join_normalized_count": collection_stats.get("join_normalized_count", 0),
                "join_unresolved_count": collection_stats.get("join_unresolved_count", 0),
                "runs_with_missing_odds": missing_odds,
                "runs_with_missing_identity": weak_identity,
                "runs_with_ambiguous_competition": ambiguous_comp,
                "pending_runs": len(pending_views),
            },
            "fetch_probe_by_pool": pool_probe,
            "result_source_diagnostics": result_source,
            "unresolved_reason_buckets": dict(reason_buckets),
            "per_run": [asdict(d) for d in per_run],
            "blocker_analysis": _blocker_analysis(
                match_results_in_wave=match_results_in_wave,
                pool_probe=pool_probe,
                reason_buckets=reason_buckets,
                total_saved=total_saved,
                settled_count=len(records),
                result_source=result_source,
            ),
        }
    finally:
        repo.close()


def _blocker_analysis(
    *,
    match_results_in_wave: int,
    pool_probe: Dict[str, Dict[str, int]],
    reason_buckets: Counter,
    total_saved: int,
    settled_count: int,
    result_source: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    in_range_total = sum(
        int(p.get("fixtures_in_range") or p.get("fixtures_in_range", 0) or 0)
        for p in pool_probe.values()
    )
    finished_total = sum(int(p.get("fixtures_finished_in_list") or 0) for p in pool_probe.values())
    fixtures_returned = int(
        (result_source or {}).get("results_endpoint_rows_returned_total")
        or (result_source or {}).get("fixtures_returned_total")
        or (result_source or {}).get("results_endpoint_rows_returned")
        or 0
    )

    if match_results_in_wave == 0:
        scraper_primary = (result_source or {}).get("primary_blocker")
        scraper_notes = (result_source or {}).get("scraper_limitations") or []
        if scraper_primary == "scraper_returned_only_future_fixtures":
            primary = "scraper_returned_only_future_fixtures"
            message = (
                f"В БД нет match_results. Scraper вернул {fixtures_returned} rows, "
                "но все kickoff даты позже окна волны (legacy fixtures path)."
            )
        elif scraper_primary in ("results_endpoint_empty", "scraper_empty_response"):
            primary = "results_endpoint_empty"
            message = (
                "Scraper /v1/competitions/results вернул пустой ответ для пулов волны."
            )
        elif scraper_primary == "results_endpoint_error":
            primary = "results_endpoint_error"
            message = "Ошибка при вызове /v1/competitions/results."
        elif in_range_total == 0 and fixtures_returned > 0:
            primary = "results_endpoint_empty_in_range"
            message = (
                f"Scraper вернул {fixtures_returned} results rows, client-side in-range=0 — "
                "даты волны отсутствуют в ответе."
            )
        elif in_range_total == 0:
            primary = "results_fetch_no_in_range_fixtures"
            message = (
                "В БД нет match_results; discovery probe не видит in-range fixtures."
            )
        elif finished_total == 0:
            primary = "scraper_returned_in_range_but_not_finished"
            message = (
                "В БД нет match_results; fixtures in-range есть, но status!=finished и нет scores."
            )
        else:
            primary = "results_not_persisted"
            message = (
                "В БД нет match_results, хотя probe видит finished fixtures — "
                "update-results не сохранил результаты."
            )
        if scraper_notes:
            message = f"{message} {' '.join(scraper_notes[:2])}"
    elif settled_count == 0 and reason_buckets.get(REASON_JOIN_MISS, 0) + reason_buckets.get(REASON_TEAM_MISMATCH, 0) > 0:
        primary = "join_broken_with_match_results"
        message = (
            "match_results есть, но join к predictions не сходится — "
            "проверьте team names / match_date identity."
        )
    elif settled_count > 0:
        primary = "partial_settlement"
        message = f"Частичный settlement: {settled_count}/{total_saved} evaluable."
    else:
        primary = "unknown_blocker"
        message = "Settlement заблокирован; см. unresolved_reason_buckets."

    return {
        "primary_blocker": primary,
        "message": message,
        "db_has_match_results": match_results_in_wave > 0,
        "probe_in_range_fixtures": in_range_total,
        "probe_finished_in_list": finished_total,
    }


def build_wave_quality_report(
    manifest: EvalWaveManifest,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Quality snapshot from current DB state (fail-soft when settled=0)."""
    views = collect_wave_predictions(manifest, db_path=db_path)
    repo = EvaluationRepositoryV2(db_path=db_path)
    try:
        rows = list(
            repo.iter_scored_runs(
                date_from=manifest.date_from,
                date_to=f"{manifest.date_to}T23:59:59",
                limit=50000,
            )
        )
        records, collection_stats = collect_settled_pool_eval_records(
            rows,
            allowed_keys=tuple(manifest.league_keys),
            repo=repo,
        )
        calibration = build_pool_calibration_review(records, collection_stats=collection_stats)
    finally:
        repo.close()

    total_saved = len(views)
    settled_views = [v for v in views if v.settle_status == "settled"]
    pending_views = [v for v in views if v.settle_status == "pending"]
    parked_views = [v for v in views if v.settle_status == "parked"]

    settled_n = len(records)
    join_exact = collection_stats.get("join_exact_count", 0)
    join_norm = collection_stats.get("join_normalized_count", 0)
    join_unres = collection_stats.get("join_unresolved_count", 0)
    join_total = join_exact + join_norm

    coverage = {
        "saved_runs": total_saved,
        "settled_runs": len(settled_views),
        "settled_evaluable_runs": settled_n,
        "unresolved_runs": len(pending_views),
        "parked_runs": len(parked_views),
        "settled_coverage": round(settled_n / total_saved, 4) if total_saved else 0.0,
        "evaluable_coverage": round(settled_n / max(1, total_saved - len(parked_views)), 4),
        "odds_coverage": round(
            sum(1 for v in views if v.best_market_odds and v.settle_status != "parked") / max(1, total_saved - len(parked_views)),
            4,
        ),
        "odds_coverage_on_settled": round(
            sum(1 for r in records if r.book_odds and r.book_odds > 1.0) / settled_n,
            4,
        )
        if settled_n
        else 0.0,
    }

    join_quality = {
        "exact_joins": join_exact,
        "normalized_joins": join_norm,
        "unresolved_joins": join_unres,
        "normalized_share_of_settled": round(join_norm / join_total, 4) if join_total else None,
        "pools_with_join_failures": _pools_with_unresolved(views, diagnostics),
    }

    market_quality = _market_quality_buckets(views, records)
    confidence_quality = _confidence_quality_buckets(views, records)
    league_quality = _league_quality_buckets(views, records)
    weak_spots = _weak_spots_summary(
        views=views,
        records=records,
        coverage=coverage,
        diagnostics=diagnostics,
        market_quality=market_quality,
        confidence_quality=confidence_quality,
        league_quality=league_quality,
    )

    insufficient = settled_n == 0
    return {
        "pipeline": "wave_quality_report",
        "wave_name": manifest.wave_name,
        "insufficient_settled_sample": insufficient,
        "coverage": coverage,
        "join_quality": join_quality,
        "quality_by_market": market_quality,
        "quality_by_confidence": confidence_quality,
        "quality_by_league": league_quality,
        "weak_spots": weak_spots,
        "calibration_review": calibration,
        "blockers": (diagnostics or {}).get("blocker_analysis"),
        "zero_sample_explanation": _zero_sample_explanation(diagnostics) if insufficient else None,
    }


def _pools_with_unresolved(
    views: Sequence[WavePredictionView],
    diagnostics: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if diagnostics and diagnostics.get("per_run"):
        by_pool: Dict[str, int] = Counter()
        for row in diagnostics["per_run"]:
            if row.get("join_status") == "unresolved":
                by_pool[str(row.get("pool_key"))] += 1
        return [{"pool_key": k, "unresolved_count": v} for k, v in sorted(by_pool.items(), key=lambda x: -x[1])]
    by_pool = Counter(v.pool_key for v in views if v.settle_status == "pending")
    return [{"pool_key": k, "unresolved_count": v} for k, v in sorted(by_pool.items(), key=lambda x: -x[1])]


def _market_quality_buckets(
    views: Sequence[WavePredictionView],
    records: Sequence[SettledPoolEvalRecord],
) -> List[Dict[str, Any]]:
    by_market_views: Dict[str, List[WavePredictionView]] = {}
    for v in views:
        if v.settle_status == "parked" or not v.best_market_key:
            continue
        by_market_views.setdefault(v.best_market_key, []).append(v)

    by_market_records: Dict[str, List[SettledPoolEvalRecord]] = {}
    for r in records:
        by_market_records.setdefault(r.market_key, []).append(r)

    keys = sorted(set(by_market_views) | set(by_market_records))
    out: List[Dict[str, Any]] = []
    for key in keys:
        vlist = by_market_views.get(key, [])
        rlist = by_market_records.get(key, [])
        wins = sum(1 for r in rlist if r.outcome)
        settled_count = len(rlist)
        odds_subset = [r for r in rlist if r.book_odds and r.book_odds > 1.0]
        roi = (
            sum((r.book_odds - 1.0) if r.outcome else -1.0 for r in odds_subset) / len(odds_subset)
            if odds_subset
            else None
        )
        out.append(
            {
                "market_key": key,
                "count": len(vlist),
                "settled_count": settled_count,
                "hit_rate": round(wins / settled_count, 4) if settled_count else None,
                "avg_confidence": round(sum(v.confidence or 0 for v in vlist) / len(vlist), 4) if vlist else None,
                "avg_predicted_prob": round(sum(r.probability for r in rlist) / settled_count, 4) if settled_count else None,
                "odds_coverage": round(len(odds_subset) / settled_count, 4) if settled_count else None,
                "roi_mean_profit": round(roi, 4) if roi is not None else None,
            }
        )
    return out


def _confidence_quality_buckets(
    views: Sequence[WavePredictionView],
    records: Sequence[SettledPoolEvalRecord],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for lo, hi, label in WAVE_CONFIDENCE_BUCKETS:
        vlist = [
            v
            for v in views
            if v.settle_status != "parked"
            and v.confidence is not None
            and (
                (lo <= v.confidence < hi)
                or (label == "0.80+" and lo <= v.confidence <= hi)
            )
        ]
        rlist = [
            r for r in records
            if lo <= r.confidence < hi or (label == "0.80+" and lo <= r.confidence <= hi)
        ]
        wins = sum(1 for r in rlist if r.outcome)
        settled_n = len(rlist)
        avg_prob = sum(r.probability for r in rlist) / settled_n if settled_n else None
        hit = wins / settled_n if settled_n else None
        odds_subset = [r for r in rlist if r.book_odds and r.book_odds > 1.0]
        roi = (
            sum((r.book_odds - 1.0) if r.outcome else -1.0 for r in odds_subset) / len(odds_subset)
            if odds_subset
            else None
        )
        out.append(
            {
                "confidence_bucket": label,
                "count": len(vlist),
                "settled_count": settled_n,
                "hit_rate": round(hit, 4) if hit is not None else None,
                "avg_predicted_probability": round(avg_prob, 4) if avg_prob is not None else None,
                "calibration_gap": round(avg_prob - hit, 4) if avg_prob is not None and hit is not None else None,
                "roi_mean_profit": round(roi, 4) if roi is not None else None,
            }
        )
    return out


def _league_quality_buckets(
    views: Sequence[WavePredictionView],
    records: Sequence[SettledPoolEvalRecord],
) -> List[Dict[str, Any]]:
    by_pool: Dict[str, List[WavePredictionView]] = {}
    for v in views:
        if v.settle_status == "parked":
            continue
        by_pool.setdefault(v.pool_key, []).append(v)

    by_pool_records: Dict[str, List[SettledPoolEvalRecord]] = {}
    for r in records:
        by_pool_records.setdefault(r.pool_key, []).append(r)

    out: List[Dict[str, Any]] = []
    for pool_key in sorted(by_pool.keys()):
        vlist = by_pool[pool_key]
        rlist = by_pool_records.get(pool_key, [])
        wins = sum(1 for r in rlist if r.outcome)
        settled_count = len(rlist)
        unresolved = sum(1 for v in vlist if v.settle_status == "pending")
        out.append(
            {
                "pool_key": pool_key,
                "count": len(vlist),
                "settled_count": settled_count,
                "hit_rate": round(wins / settled_count, 4) if settled_count else None,
                "unresolved_share": round(unresolved / len(vlist), 4) if vlist else 0.0,
                "odds_coverage": round(
                    sum(1 for v in vlist if v.best_market_odds) / len(vlist), 4
                )
                if vlist
                else 0.0,
                "low_confidence_share": round(
                    sum(1 for v in vlist if (v.confidence or 0) < LOW_CONFIDENCE_THRESHOLD) / len(vlist),
                    4,
                )
                if vlist
                else 0.0,
            }
        )
    return out


def _weak_spots_summary(
    *,
    views: Sequence[WavePredictionView],
    records: Sequence[SettledPoolEvalRecord],
    coverage: Dict[str, Any],
    diagnostics: Optional[Dict[str, Any]],
    market_quality: List[Dict[str, Any]],
    confidence_quality: List[Dict[str, Any]],
    league_quality: List[Dict[str, Any]],
) -> List[str]:
    bullets: List[str] = []

    if coverage.get("settled_evaluable_runs", 0) == 0:
        blocker = (diagnostics or {}).get("blocker_analysis", {})
        if blocker.get("message"):
            bullets.append(blocker["message"])
        buckets = (diagnostics or {}).get("unresolved_reason_buckets", {})
        if buckets:
            top = max(buckets.items(), key=lambda x: x[1])
            bullets.append(f"Главная причина unresolved: `{top[0]}` ({top[1]} runs).")
        return bullets[:7]

    if records:
        overall_hit = sum(1 for r in records if r.outcome) / len(records)
        bullets.append(f"Общий hit rate на settled subset: {overall_hit:.1%} (n={len(records)}).")

    bad_markets = [m for m in market_quality if m.get("settled_count", 0) >= 3 and (m.get("hit_rate") or 1) < 0.35]
    for m in sorted(bad_markets, key=lambda x: x.get("hit_rate") or 0)[:2]:
        bullets.append(
            f"Слабый market `{m['market_key']}`: hit={m.get('hit_rate')} (n={m.get('settled_count')})."
        )

    high_unresolved = sorted(league_quality, key=lambda x: x.get("unresolved_share", 0), reverse=True)
    if high_unresolved and high_unresolved[0].get("unresolved_share", 0) > 0.3:
        h = high_unresolved[0]
        bullets.append(f"Много unresolved в `{h['pool_key']}`: {h.get('unresolved_share', 0):.0%}.")

    low_odds = [lg for lg in league_quality if lg.get("odds_coverage", 1) < 0.4]
    if low_odds:
        bullets.append(f"Низкое odds coverage в: {', '.join(lg['pool_key'] for lg in low_odds[:3])}.")

    for bucket in confidence_quality:
        gap = bucket.get("calibration_gap")
        if bucket.get("settled_count", 0) >= 3 and gap is not None and gap >= 0.12:
            bullets.append(
                f"Перекос confidence в bucket {bucket['confidence_bucket']}: "
                f"avg_prob выше hit на {gap:+.2f}."
            )
        if bucket.get("settled_count", 0) >= 3 and gap is not None and gap <= -0.12:
            bullets.append(
                f"Заниженная confidence в bucket {bucket['confidence_bucket']}: "
                f"hit выше avg_prob на {-gap:+.2f}."
            )

    suspicious = [
        m for m in market_quality
        if m.get("count", 0) >= 5 and m.get("settled_count", 0) == 0
    ]
    if suspicious:
        bullets.append(
            f"Подозрительно: markets без settlement при наличии прогнозов: "
            f"{', '.join(m['market_key'] for m in suspicious[:3])}."
        )

    return bullets[:7]


def _zero_sample_explanation(diagnostics: Optional[Dict[str, Any]]) -> Optional[str]:
    if not diagnostics:
        return "Settled sample=0; запустите diagnose-wave для детализации blockers."
    blocker = diagnostics.get("blocker_analysis") or {}
    return blocker.get("message")


def format_diagnostics_markdown(manifest: EvalWaveManifest, payload: Dict[str, Any]) -> str:
    lines = [
        f"# Wave settlement diagnostics — {manifest.label}",
        "",
        f"- **Wave:** `{manifest.wave_name}`",
        f"- **Generated:** {payload.get('generated_at_utc', '')}",
        "",
        "## Summary",
        "",
    ]
    summary = payload.get("summary") or {}
    for key, val in summary.items():
        lines.append(f"- {key}: **{val}**")

    lines.extend(["", "## Blocker analysis", ""])
    blocker = payload.get("blocker_analysis") or {}
    lines.append(f"- Primary: **{blocker.get('primary_blocker', '—')}**")
    if blocker.get("message"):
        lines.append(f"- {blocker['message']}")

    lines.extend(["", "## Unresolved reason buckets", ""])
    for reason, count in sorted((payload.get("unresolved_reason_buckets") or {}).items(), key=lambda x: -x[1]):
        lines.append(f"- `{reason}`: **{count}**")

    lines.extend(["", "## Per-run (unresolved)", ""])
    lines.append("| run_id | date | pool | home | away | reason | detail |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in payload.get("per_run") or []:
        if row.get("join_status") != "unresolved":
            continue
        lines.append(
            f"| `{row.get('run_id', '')[:8]}…` | {row.get('date')} | {row.get('pool_key')} | "
            f"{row.get('home')} | {row.get('away')} | `{row.get('unresolved_reason')}` | "
            f"{row.get('detail', '')[:60]} |"
        )
    return "\n".join(lines)


def format_quality_markdown(manifest: EvalWaveManifest, payload: Dict[str, Any]) -> str:
    lines = [
        f"# Wave quality audit — {manifest.label}",
        "",
        f"- **Wave:** `{manifest.wave_name}`",
        "",
        "## Coverage",
        "",
    ]
    for key, val in (payload.get("coverage") or {}).items():
        lines.append(f"- {key}: **{val}**")

    lines.extend(["", "## Join quality", ""])
    for key, val in (payload.get("join_quality") or {}).items():
        lines.append(f"- {key}: **{val}**")

    if payload.get("insufficient_settled_sample"):
        lines.extend(["", "## Zero settled sample", ""])
        if payload.get("zero_sample_explanation"):
            lines.append(payload["zero_sample_explanation"])

    lines.extend(["", "## Weak spots", ""])
    for bullet in payload.get("weak_spots") or []:
        lines.append(f"- {bullet}")

    return "\n".join(lines)


def write_diagnostics_artifacts(
    manifest: EvalWaveManifest,
    diagnostics: Dict[str, Any],
    quality: Optional[Dict[str, Any]] = None,
    *,
    output_dir: Path = EVAL_WAVE_REPORTS_DIR,
) -> Dict[str, str]:
    ensure_runtime_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"{manifest.wave_name}_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, str] = {}
    diag_json = output_dir / f"{base}_diagnostics.json"
    diag_md = output_dir / f"{base}_diagnostics.md"
    diag_json.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    diag_md.write_text(format_diagnostics_markdown(manifest, diagnostics), encoding="utf-8")
    paths["diagnostics_json"] = str(diag_json)
    paths["diagnostics_markdown"] = str(diag_md)

    if quality is not None:
        qual_json = output_dir / f"{base}_audit.json"
        qual_md = output_dir / f"{base}_audit.md"
        qual_json.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
        qual_md.write_text(format_quality_markdown(manifest, quality), encoding="utf-8")
        paths["audit_json"] = str(qual_json)
        paths["audit_markdown"] = str(qual_md)

    return paths
