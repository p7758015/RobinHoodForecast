"""
Pragmatic calibration review for league eval-pool (report layer only).

Operates on persisted pool-scoped, settled, league-scored runs — no scorer re-run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from football_agent.eval_pool.report import _count_odds_markets, _in_pool_scope, _snapshot_meta
from football_agent.eval_pool.scope import LOW_CONFIDENCE_THRESHOLD, resolve_pool_entry
from football_agent.offline.evaluation_v2 import (
    extract_settlement_identity,
    resolve_match_result,
    settle_best_market,
)
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2, EvaluationRunRow

# Operational confidence buckets (overall_confidence_score).
POOL_CONFIDENCE_BUCKETS: Tuple[Tuple[float, float, str], ...] = (
    (0.50, 0.60, "0.50-0.59"),
    (0.60, 0.70, "0.60-0.69"),
    (0.70, 0.80, "0.70-0.79"),
    (0.80, 1.01, "0.80+"),
)

TRACKED_RISK_FLAGS = frozenset(
    {
        "new_coach",
        "coach_bounce_window",
        "pre_big_match_risk",
        "post_big_match_fatigue",
        "thin_squad",
        "schedule_risk",
        "poor_form",
        "strong_form",
        "low_confidence_data",
    },
)

DERIVED_RISK_TAGS = frozenset(
    {
        "low_squad_confidence",
        "low_schedule_confidence",
        "no_best_market_odds",
        "no_snapshot_odds",
        "partial_odds_link",
    },
)

MIN_SETTLED_FOR_DIAGNOSTICS = 8
MIN_SETTLED_RECOMMENDED = 30


@dataclass
class SettledPoolEvalRecord:
    run_id: str
    pool_key: str
    competition_name: str
    market_key: str
    probability: float
    confidence: float
    book_odds: Optional[float]
    outcome: bool
    has_snapshot_odds: bool
    risk_tags: Tuple[str, ...] = field(default_factory=tuple)


def pool_confidence_bucket_label(confidence: float) -> Optional[str]:
    for lo, hi, label in POOL_CONFIDENCE_BUCKETS:
        if lo <= confidence < hi or (label == "0.80+" and lo <= confidence <= hi):
            return label
    return None


def extract_risk_tags(
    *,
    prediction: dict,
    snapshot_json: Optional[dict],
    report_json: Optional[dict],
) -> List[str]:
    tags: List[str] = []

    for side in ("home_scoring", "away_scoring"):
        scoring = prediction.get(side) if isinstance(prediction.get(side), dict) else {}
        flags = scoring.get("summary_flags") if isinstance(scoring, dict) else None
        if isinstance(flags, list):
            for flag in flags:
                name = str(flag).strip()
                if name in TRACKED_RISK_FLAGS:
                    tags.append(name)
        factor_scores = scoring.get("factor_scores") if isinstance(scoring, dict) else None
        if isinstance(factor_scores, dict):
            squad = factor_scores.get("squad_availability")
            if isinstance(squad, (int, float)) and squad < 0.45:
                tags.append("low_squad_confidence")
            sched = factor_scores.get("schedule_context")
            if isinstance(sched, (int, float)) and sched < 0.45:
                tags.append("low_schedule_confidence")

    best = prediction.get("best_market") if isinstance(prediction.get("best_market"), dict) else {}
    book_odds = best.get("book_odds")
    if not isinstance(book_odds, (int, float)) or book_odds <= 1.0:
        tags.append("no_best_market_odds")

    if _count_odds_markets(snapshot_json if isinstance(snapshot_json, dict) else None) == 0:
        tags.append("no_snapshot_odds")

    if isinstance(report_json, dict):
        odds_link = report_json.get("odds_link_strategy")
        if isinstance(odds_link, str) and odds_link in ("partial", "failed", "none"):
            tags.append("partial_odds_link")

    # stable order for tests
    return sorted(set(tags))


def collect_settled_pool_eval_records(
    rows: Sequence[EvaluationRunRow],
    *,
    allowed_keys: Sequence[str],
    repo: EvaluationRepositoryV2,
) -> Tuple[List[SettledPoolEvalRecord], Dict[str, int]]:
    """
    Pool-scoped evaluable records with settlement outcome.

    Excludes analysis_only / parked runs.
    """
    stats = {
        "pool_rows_seen": 0,
        "league_scored": 0,
        "parked_skipped": 0,
        "out_of_pool_skipped": 0,
        "identity_missing": 0,
        "unsettled": 0,
        "no_best_market": 0,
        "settlement_incompatible": 0,
        "settled_evaluable": 0,
    }
    records: List[SettledPoolEvalRecord] = []

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
            stats["out_of_pool_skipped"] += 1
            continue

        stats["pool_rows_seen"] += 1
        pred = row.prediction_json if isinstance(row.prediction_json, dict) else {}
        if pred.get("analysis_mode") == "analysis_only":
            stats["parked_skipped"] += 1
            continue

        stats["league_scored"] += 1
        entry = resolve_pool_entry(
            str(comp_name) if comp_name else None,
            str(comp_country) if comp_country else None,
        )
        pool_key = entry.key if entry else "unknown"

        identity = extract_settlement_identity(
            snapshot_json=snap,
            run_home_team=row.home_team,
            run_away_team=row.away_team,
            run_kickoff_utc=row.kickoff_utc,
        )
        if identity is None:
            stats["identity_missing"] += 1
            continue

        settlement = resolve_match_result(
            match_date=identity.match_date,
            home_team=identity.home_team,
            away_team=identity.away_team,
            exact_lookup=repo.fetch_match_result_exact,
            date_lookup=repo.fetch_match_results_for_date,
        )
        if not settlement.resolved:
            stats["unsettled"] += 1
            continue

        best = pred.get("best_market")
        if not isinstance(best, dict):
            stats["no_best_market"] += 1
            continue

        market_key = best.get("market_key")
        probability = best.get("probability")
        if not isinstance(market_key, str) or not isinstance(probability, (int, float)):
            stats["no_best_market"] += 1
            continue

        outcome = settle_best_market(market_key, int(settlement.home_score), int(settlement.away_score))  # type: ignore[arg-type]
        if outcome is None:
            stats["settlement_incompatible"] += 1
            continue

        conf = float(pred.get("overall_confidence_score") or 0.0)
        book_odds = best.get("book_odds")
        bo = float(book_odds) if isinstance(book_odds, (int, float)) else None

        records.append(
            SettledPoolEvalRecord(
                run_id=row.run_id,
                pool_key=pool_key,
                competition_name=str(comp_name) if comp_name else pool_key,
                market_key=market_key,
                probability=float(probability),
                confidence=conf,
                book_odds=bo,
                outcome=bool(outcome),
                has_snapshot_odds=_count_odds_markets(snap) > 0,
                risk_tags=tuple(
                    extract_risk_tags(
                        prediction=pred,
                        snapshot_json=snap,
                        report_json=row.report_json if isinstance(row.report_json, dict) else None,
                    )
                ),
            )
        )
        stats["settled_evaluable"] += 1

    return records, stats


def _aggregate_bucket(
    records: Sequence[SettledPoolEvalRecord],
    *,
    key_fn: Callable[[SettledPoolEvalRecord], Optional[str]],
) -> List[Dict[str, Any]]:
    groups: Dict[str, List[SettledPoolEvalRecord]] = {}
    for rec in records:
        key = key_fn(rec)
        if not key:
            continue
        groups.setdefault(key, []).append(rec)

    out: List[Dict[str, Any]] = []
    for key in sorted(groups.keys()):
        items = groups[key]
        wins = sum(1 for r in items if r.outcome)
        count = len(items)
        probs = [r.probability for r in items]
        confs = [r.confidence for r in items]
        odds_vals = [r.book_odds for r in items if r.book_odds and r.book_odds > 1.0]
        roi_profit = sum((r.book_odds - 1.0) if r.outcome else -1.0 for r in items if r.book_odds and r.book_odds > 1.0)

        out.append(
            {
                "key": key,
                "count": count,
                "hit_rate": round(wins / count, 4) if count else None,
                "avg_predicted_probability": round(sum(probs) / count, 4) if count else None,
                "avg_confidence": round(sum(confs) / count, 4) if count else None,
                "avg_book_odds": round(sum(odds_vals) / len(odds_vals), 4) if odds_vals else None,
                "roi_subset_count": len(odds_vals),
                "roi_mean_profit": round(roi_profit / len(odds_vals), 4) if odds_vals else None,
            }
        )
    return out


def build_pool_calibration_review(
    records: Sequence[SettledPoolEvalRecord],
    *,
    collection_stats: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    settled_n = len(records)
    low_conf_share = (
        round(sum(1 for r in records if r.confidence < LOW_CONFIDENCE_THRESHOLD) / settled_n, 4)
        if settled_n
        else 0.0
    )

    confidence_buckets = _aggregate_bucket(
        records,
        key_fn=lambda r: pool_confidence_bucket_label(r.confidence),
    )
    # rename key field for readability
    for b in confidence_buckets:
        b["confidence_bucket"] = b.pop("key")

    market_buckets = _aggregate_bucket(records, key_fn=lambda r: r.market_key)
    for b in market_buckets:
        b["market_key"] = b.pop("key")

    league_buckets = _aggregate_bucket(records, key_fn=lambda r: r.pool_key)
    for b in league_buckets:
        b["pool_key"] = b.pop("key")
        sample = next((r for r in records if r.pool_key == b["pool_key"]), None)
        if sample:
            b["competition_name"] = sample.competition_name
        items = [r for r in records if r.pool_key == b["pool_key"]]
        if items:
            b["settled_count"] = len(items)
            b["odds_coverage_share"] = round(
                sum(1 for r in items if r.book_odds and r.book_odds > 1.0) / len(items),
                4,
            )
            b["low_confidence_share"] = round(
                sum(1 for r in items if r.confidence < LOW_CONFIDENCE_THRESHOLD) / len(items),
                4,
            )

    risk_buckets = _aggregate_risk_flag_buckets(records)
    diagnostics = _build_diagnostics(records, confidence_buckets, market_buckets, league_buckets)

    return {
        "pipeline": "league_eval_pool_calibration",
        "sample": {
            "settled_evaluable_runs": settled_n,
            "min_recommended_for_review": MIN_SETTLED_RECOMMENDED,
            "sufficient_for_diagnostics": settled_n >= MIN_SETTLED_FOR_DIAGNOSTICS,
            "low_confidence_share": low_conf_share,
            "collection_stats": collection_stats or {},
        },
        "coverage": _coverage_summary(records, collection_stats or {}),
        "confidence_buckets": confidence_buckets,
        "market_buckets": market_buckets,
        "league_buckets": league_buckets,
        "risk_flag_buckets": risk_buckets,
        "diagnostics": diagnostics,
    }


def _coverage_summary(
    records: Sequence[SettledPoolEvalRecord],
    collection_stats: Dict[str, int],
) -> Dict[str, Any]:
    settled_n = len(records)
    with_odds = sum(1 for r in records if r.book_odds and r.book_odds > 1.0)
    with_snap_odds = sum(1 for r in records if r.has_snapshot_odds)
    return {
        "settled_evaluable_runs": settled_n,
        "runs_with_best_market_odds": with_odds,
        "runs_with_snapshot_odds": with_snap_odds,
        "parked_skipped": collection_stats.get("parked_skipped", 0),
        "unsettled_league_scored": collection_stats.get("unsettled", 0),
        "out_of_pool_skipped": collection_stats.get("out_of_pool_skipped", 0),
    }


def _aggregate_risk_flag_buckets(records: Sequence[SettledPoolEvalRecord]) -> List[Dict[str, Any]]:
    flag_records: Dict[str, List[SettledPoolEvalRecord]] = {}
    baseline_n = len(records)

    for rec in records:
        if not rec.risk_tags:
            flag_records.setdefault("(none)", []).append(rec)
            continue
        for tag in rec.risk_tags:
            flag_records.setdefault(tag, []).append(rec)

    rows: List[Dict[str, Any]] = []
    for tag in sorted(flag_records.keys()):
        items = flag_records[tag]
        wins = sum(1 for r in items if r.outcome)
        count = len(items)
        rows.append(
            {
                "flag": tag,
                "count": count,
                "share_of_settled": round(count / baseline_n, 4) if baseline_n else 0.0,
                "hit_rate": round(wins / count, 4) if count else None,
                "diagnostic": _risk_flag_diagnostic(tag, count, wins),
            }
        )
    return rows


def _risk_flag_diagnostic(tag: str, count: int, wins: int) -> str:
    if count < 3:
        return "мало наблюдений"
    hit = wins / count
    if tag in ("no_best_market_odds", "no_snapshot_odds", "partial_odds_link"):
        return "проверить качество линии / enrichment"
    if tag in ("thin_squad", "low_squad_confidence"):
        return "слабый состав — осторожность с уверенностью"
    if tag in ("pre_big_match_risk", "post_big_match_fatigue", "schedule_risk"):
        return "календарный риск — возможен overconfidence"
    if tag == "new_coach":
        return "новый тренер — повышенная дисперсия"
    if hit < 0.45:
        return "низкий hit rate в срезе"
    if hit > 0.65:
        return "срез бьёт выше среднего"
    return "в пределах ожиданий"


def _build_diagnostics(
    records: Sequence[SettledPoolEvalRecord],
    confidence_buckets: List[Dict[str, Any]],
    market_buckets: List[Dict[str, Any]],
    league_buckets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    settled_n = len(records)
    if settled_n < MIN_SETTLED_FOR_DIAGNOSTICS:
        return {
            "status": "insufficient_sample",
            "message": (
                f"Settled evaluable runs={settled_n} — мало для надёжных выводов. "
                f"Рекомендуется ≥{MIN_SETTLED_RECOMMENDED}."
            ),
            "findings": [],
        }

    findings: List[Dict[str, Any]] = []

    for bucket in confidence_buckets:
        count = int(bucket.get("count") or 0)
        if count < 3:
            continue
        pred_avg = bucket.get("avg_predicted_probability")
        hit = bucket.get("hit_rate")
        if pred_avg is None or hit is None:
            continue
        gap = float(pred_avg) - float(hit)
        label = bucket.get("confidence_bucket", "?")
        if gap >= 0.12:
            findings.append(
                {
                    "type": "overconfidence",
                    "scope": f"confidence:{label}",
                    "detail": f"avg prob {pred_avg:.2f} vs hit {hit:.2f} (gap {gap:+.2f})",
                    "suggestion": "рассмотреть shrink / порог express для этого bucket",
                }
            )
        elif gap <= -0.10:
            findings.append(
                {
                    "type": "underconfidence",
                    "scope": f"confidence:{label}",
                    "detail": f"avg prob {pred_avg:.2f} vs hit {hit:.2f} (gap {gap:+.2f})",
                    "suggestion": "bucket бьёт лучше модели — возможен запас по порогам",
                }
            )

    if settled_n >= 5 and market_buckets:
        top = max(market_buckets, key=lambda b: b.get("count") or 0)
        top_share = (top.get("count") or 0) / settled_n
        if top_share >= 0.55:
            findings.append(
                {
                    "type": "market_concentration",
                    "scope": top.get("market_key"),
                    "detail": f"{top.get('market_key')} = {top_share:.0%} выборки",
                    "suggestion": "проверить market selection bias / express mix",
                }
            )

    if len(league_buckets) >= 2:
        rates = [(b["pool_key"], b.get("hit_rate"), b.get("count") or 0) for b in league_buckets]
        eligible = [(k, h, c) for k, h, c in rates if c >= 3 and h is not None]
        if len(eligible) >= 2:
            best = max(eligible, key=lambda x: x[1])
            worst = min(eligible, key=lambda x: x[1])
            spread = float(best[1]) - float(worst[1])
            if spread >= 0.20:
                findings.append(
                    {
                        "type": "league_bias",
                        "scope": f"{worst[0]} vs {best[0]}",
                        "detail": f"hit spread {spread:.2f} ({worst[1]:.2f} .. {best[1]:.2f})",
                        "suggestion": "лига-специфичный шум или data quality — не смешивать в одном пороге",
                    }
                )

    overall_hit = sum(1 for r in records if r.outcome) / settled_n
    overall_conf = sum(r.confidence for r in records) / settled_n
    if overall_conf - overall_hit >= 0.10:
        findings.append(
            {
                "type": "overconfidence",
                "scope": "overall",
                "detail": f"mean confidence {overall_conf:.2f} vs hit {overall_hit:.2f}",
                "suggestion": "глобальный shrink confidence / express thresholds",
            }
        )

    return {
        "status": "ok" if findings else "no_strong_signals",
        "message": None if findings else "Явных перекосов не найдено на текущей выборке.",
        "findings": findings,
    }


def format_calibration_cli_summary(review: Dict[str, Any]) -> str:
    lines = ["League eval-pool calibration review"]
    sample = review.get("sample") or {}
    lines.append(f"- settled evaluable runs: {sample.get('settled_evaluable_runs')}")
    lines.append(f"- sufficient for diagnostics: {sample.get('sufficient_for_diagnostics')}")
    lines.append(f"- low confidence share: {sample.get('low_confidence_share')}")

    lines.append("")
    lines.append("Confidence buckets:")
    for b in review.get("confidence_buckets") or []:
        lines.append(
            f"  • {b.get('confidence_bucket')}: n={b.get('count')} "
            f"hit={b.get('hit_rate')} avg_prob={b.get('avg_predicted_probability')} "
            f"roi={b.get('roi_mean_profit')}"
        )

    lines.append("")
    lines.append("Market buckets:")
    for b in review.get("market_buckets") or []:
        lines.append(
            f"  • {b.get('market_key')}: n={b.get('count')} hit={b.get('hit_rate')} "
            f"avg_conf={b.get('avg_confidence')}"
        )

    lines.append("")
    lines.append("League buckets:")
    for b in review.get("league_buckets") or []:
        lines.append(
            f"  • {b.get('pool_key')}: n={b.get('count')} hit={b.get('hit_rate')} "
            f"odds_n={b.get('roi_subset_count')}"
        )

    diag = review.get("diagnostics") or {}
    lines.append("")
    lines.append(f"Diagnostics: {diag.get('status')}")
    if diag.get("message"):
        lines.append(f"  {diag['message']}")
    for f in diag.get("findings") or []:
        lines.append(f"  • [{f.get('type')}] {f.get('scope')}: {f.get('detail')}")

    return "\n".join(lines)
