"""Factor-level diagnostics for LeagueScorerV2 (debug / evaluation only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, TeamScoringResultV2
from football_agent.scorers.league_factor_weights import resolve_weights_from_snapshot
from football_agent.scorers.league_scorer_v2 import HOME_ADVANTAGE, LeagueScorerV2
from football_agent.services.scoring_service_v2 import ScoredRunV2


def _weighted_contributions(
    scoring: TeamScoringResultV2,
    weights: Dict[str, float],
) -> Dict[str, float]:
    fs = scoring.factor_scores
    raw = {
        "baseline_strength": weights.get("baseline_strength", 0) * fs.baseline_strength,
        "current_form": weights.get("current_form", 0) * fs.current_form,
        "motivation": weights.get("motivation", 0) * fs.motivation,
        "squad_availability": weights.get("squad_availability", 0) * fs.squad_availability,
        "coach_factor": weights.get("coach_factor", 0) * fs.coach_factor,
        "schedule_context": weights.get("schedule_context", 0) * fs.schedule_context,
        "h2h_context_bias": weights.get("h2h_context_bias", 0) * fs.h2h_context_bias,
    }
    total = sum(raw.values()) or 1.0
    return {k: round(v, 4) for k, v in raw.items()} | {
        "share_pct": {k: round(100.0 * v / total, 1) for k, v in raw.items()},
        "total_weighted_sum": round(total, 4),
    }


def build_team_inspection(
    snapshot: MatchAnalysisSnapshotV2,
    scoring: TeamScoringResultV2,
    *,
    side: str,
) -> Dict[str, Any]:
    resolved = resolve_weights_from_snapshot(snapshot, side=side)
    fs = scoring.factor_scores
    contrib = _weighted_contributions(scoring, resolved.weights)
    return {
        "team": scoring.team.name,
        "season_phase": resolved.phase.value,
        "special_overrides": list(resolved.special_overrides_applied),
        "effective_weights": {k: round(v, 4) for k, v in resolved.weights.items()},
        "factor_scores": fs.model_dump(mode="json"),
        "weighted_contributions": contrib,
        "summary_flags": list(scoring.summary_flags),
    }


def build_scorer_inspection(scored: ScoredRunV2) -> Dict[str, Any]:
    snap = scored.snapshot
    pred = scored.prediction
    home_r = pred.home_scoring.factor_scores.total_score + HOME_ADVANTAGE
    away_r = pred.away_scoring.factor_scores.total_score
    inspection: Dict[str, Any] = {
        "scorer_name": scored.scorer_name,
        "scoring_skipped": scored.scoring_skipped,
        "analysis_mode": pred.analysis_mode,
        "prediction_mode": pred.prediction_mode,
        "overall_confidence": round(pred.overall_confidence_score, 4),
        "snapshot_confidence": snap.confidence.model_dump(mode="json"),
        "team_strength_r": {
            "home": round(home_r, 4),
            "away": round(away_r, 4),
            "balance": round(abs(home_r - away_r), 4),
        },
        "home": build_team_inspection(snap, pred.home_scoring, side="home"),
        "away": build_team_inspection(snap, pred.away_scoring, side="away"),
    }
    if scored.routing_decision is not None:
        rd = scored.routing_decision
        inspection["routing"] = {
            "route": rd.route,
            "reason": rd.reason,
            "tournament_type": rd.tournament_type.value,
            "category": rd.category.value,
        }
    if pred.parked_context is not None:
        inspection["parked_context"] = pred.parked_context.model_dump(mode="json")
    if pred.best_market is not None:
        bm = pred.best_market
        inspection["best_market"] = {
            "market_key": bm.market_key,
            "probability": round(bm.probability, 4),
            "book_odds": bm.book_odds,
            "edge": bm.edge,
        }
    inspection["express_safety"] = pred.express_safety.model_dump(mode="json")
    top_markets = sorted(pred.market_predictions, key=lambda m: m.probability, reverse=True)[:4]
    inspection["top_markets"] = [
        {"market_key": m.market_key, "probability": round(m.probability, 4)} for m in top_markets
    ]
    return inspection


def rescore_snapshot(snapshot: MatchAnalysisSnapshotV2):
    """Convenience: score + inspection dict."""
    from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
    from football_agent.services.scoring_service_v2 import ScoringServiceV2

    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, BuildReport())
    return scored, build_scorer_inspection(scored)
