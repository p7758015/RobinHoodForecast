"""
Simple evaluation metrics (Phase Evaluation A groundwork).

Operates on MatchOddsCoverage / EvaluationGroundworkRecord collections.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from football_agent.evaluation.groundwork_models import EvaluationGroundworkRecord
from football_agent.odds.coverage_models import COVERAGE_MARKET_KEYS, MatchOddsCoverage
from football_agent.offline.evaluation_v2 import CALIBRATION_BUCKETS, bucket_index
from football_agent.offline.market_outcomes import evaluate_market_outcome


def compute_odds_coverage_metrics(
    coverages: Sequence[MatchOddsCoverage],
) -> Dict[str, Any]:
    """Aggregate odds coverage rates across a sample of matches."""
    n = len(coverages)
    if n == 0:
        return {
            "sample_size": 0,
            "any_odds_rate": 0.0,
            "parlay_usable_rate": 0.0,
            "by_group": {},
            "by_market": {},
        }

    def rate(flag_fn) -> float:
        return round(sum(1 for c in coverages if flag_fn(c)) / n, 4)

    by_market: Dict[str, float] = {}
    for key in COVERAGE_MARKET_KEYS:
        by_market[key] = round(
            sum(1 for c in coverages if c.markets.get(key) and c.markets[key].has_odds) / n,
            4,
        )

    return {
        "sample_size": n,
        "any_odds_rate": rate(lambda c: c.has_any_odds),
        "parlay_usable_rate": rate(lambda c: c.odds_usable_for_parlay),
        "by_group": {
            "1x2": rate(lambda c: c.has_1x2_odds),
            "double_chance": rate(lambda c: c.has_double_chance_odds),
            "btts": rate(lambda c: c.has_btts_odds),
            "totals": rate(lambda c: c.has_totals_odds),
        },
        "by_market": by_market,
        "avg_real_markets": round(sum(c.real_market_count for c in coverages) / n, 2),
        "avg_derived_markets": round(sum(c.derived_market_count for c in coverages) / n, 2),
    }


def compute_best_market_hit_summary(
    records: Sequence[EvaluationGroundworkRecord],
) -> Dict[str, Any]:
    """
    Best-market hit rate + per-market selection counts (groundwork).

    Only settled records with compatible best_market_key contribute to hit rate.
    """
    settled = 0
    hits = 0
    market_wins: Dict[str, int] = {}
    market_total: Dict[str, int] = {}

    for rec in records:
        actual = rec.actual_result
        pred = rec.prediction
        if actual is None or not pred.best_market_key:
            continue

        mk = pred.best_market_key
        outcome = evaluate_market_outcome(mk, actual.home_score, actual.away_score)
        if outcome is None:
            continue

        settled += 1
        market_total[mk] = market_total.get(mk, 0) + 1
        if outcome:
            hits += 1
            market_wins[mk] = market_wins.get(mk, 0) + 1

    per_market: Dict[str, Dict[str, Any]] = {}
    for mk, total in market_total.items():
        wins = market_wins.get(mk, 0)
        per_market[mk] = {
            "count": total,
            "wins": wins,
            "hit_rate": round(wins / total, 4) if total else None,
        }

    return {
        "settled_count": settled,
        "hit_count": hits,
        "hit_rate": round(hits / settled, 4) if settled else None,
        "per_market": per_market,
    }


def build_calibration_groundwork(
    samples: Sequence[Tuple[float, bool]],
) -> Dict[str, Any]:
    """
    Group (predicted_probability, won) pairs into calibration buckets.

  Interface groundwork — full calibration reports live in offline/evaluation_v2.py.
    """
    buckets = [
        {
            "range": f"[{lo:.2f},{hi:.2f})" if hi < 1.01 else f"[{lo:.2f},{hi:.2f}]",
            "count": 0,
            "predicted_sum": 0.0,
            "wins": 0,
        }
        for lo, hi in CALIBRATION_BUCKETS
    ]

    for prob, won in samples:
        bi = bucket_index(float(prob))
        if bi is None:
            continue
        buckets[bi]["count"] += 1
        buckets[bi]["predicted_sum"] += float(prob)
        buckets[bi]["wins"] += 1 if won else 0

    for b in buckets:
        if b["count"] > 0:
            b["predicted_avg"] = round(b["predicted_sum"] / b["count"], 4)
            b["actual_winrate"] = round(b["wins"] / b["count"], 4)
        else:
            b["predicted_avg"] = None
            b["actual_winrate"] = None
        del b["predicted_sum"]

    return {"buckets": buckets, "sample_size": len(samples)}


def summarize_groundwork_records(
    records: Sequence[EvaluationGroundworkRecord],
) -> Dict[str, Any]:
    """Combined report: coverage + prediction-only share + calibration on settled."""
    coverages = [r.odds_coverage for r in records]
    coverage_metrics = compute_odds_coverage_metrics(coverages)
    hit_summary = compute_best_market_hit_summary(records)

    calib_samples: List[Tuple[float, bool]] = []
    prediction_only_count = 0
    for rec in records:
        if rec.prediction_only:
            prediction_only_count += 1
        actual = rec.actual_result
        pred = rec.prediction
        if actual is None or pred.best_market_probability is None or not pred.best_market_key:
            continue
        outcome = evaluate_market_outcome(
            pred.best_market_key,
            actual.home_score,
            actual.away_score,
        )
        if outcome is None:
            continue
        calib_samples.append((pred.best_market_probability, bool(outcome)))

    n = len(records)
    return {
        "sample_size": n,
        "prediction_only_rate": round(prediction_only_count / n, 4) if n else 0.0,
        "odds_coverage": coverage_metrics,
        "best_market": hit_summary,
        "calibration": build_calibration_groundwork(calib_samples),
    }
