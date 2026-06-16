"""
Blueprint-aligned factor weights and weight-resolution helpers for LeagueScorerV2.

See docs/league_logic_blueprint.md §17 (phase table) and §18 (special overrides).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from football_agent.domain.enums_v2 import SeasonPhase
from football_agent.domain.models_v2 import ConfidenceBreakdownV2, MatchAnalysisSnapshotV2

FACTOR_KEYS = (
    "baseline_strength",
    "current_form",
    "motivation",
    "squad_availability",
    "coach_factor",
    "schedule_context",
    "h2h_context_bias",
)

# Blueprint §17 — weights sum to 1.0 per phase (including weak H2H slot).
PHASE_FACTOR_WEIGHTS: Dict[SeasonPhase, Dict[str, float]] = {
    SeasonPhase.EARLY: {
        "baseline_strength": 0.24,
        "current_form": 0.22,
        "motivation": 0.10,
        "squad_availability": 0.14,
        "coach_factor": 0.15,
        "schedule_context": 0.10,
        "h2h_context_bias": 0.05,
    },
    SeasonPhase.MID: {
        "baseline_strength": 0.20,
        "current_form": 0.24,
        "motivation": 0.16,
        "squad_availability": 0.14,
        "coach_factor": 0.12,
        "schedule_context": 0.10,
        "h2h_context_bias": 0.04,
    },
    SeasonPhase.LATE: {
        "baseline_strength": 0.15,
        "current_form": 0.22,
        "motivation": 0.24,
        "squad_availability": 0.15,
        "coach_factor": 0.10,
        "schedule_context": 0.11,
        "h2h_context_bias": 0.03,
    },
    SeasonPhase.FINAL_RUN_IN: {
        "baseline_strength": 0.12,
        "current_form": 0.20,
        "motivation": 0.28,
        "squad_availability": 0.15,
        "coach_factor": 0.10,
        "schedule_context": 0.11,
        "h2h_context_bias": 0.04,
    },
}

# Fallback when season_phase unknown — same table keyed by progress fraction.
_PROGRESS_TO_PHASE = (
    (0.25, SeasonPhase.EARLY),
    (0.70, SeasonPhase.MID),
    (0.85, SeasonPhase.LATE),
    (1.01, SeasonPhase.FINAL_RUN_IN),
)


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def resolve_season_phase(
    *,
    season_phase: Optional[SeasonPhase],
    season_progress: float,
) -> SeasonPhase:
    if season_phase is not None and season_phase != SeasonPhase.UNKNOWN:
        return season_phase
    progress = _clip01(season_progress)
    for threshold, phase in _PROGRESS_TO_PHASE:
        if progress < threshold:
            return phase
    return SeasonPhase.FINAL_RUN_IN


def base_weights_for_phase(phase: SeasonPhase) -> Dict[str, float]:
    return dict(PHASE_FACTOR_WEIGHTS.get(phase, PHASE_FACTOR_WEIGHTS[SeasonPhase.MID]))


def confidence_factor(block_confidence: float) -> float:
    """Map block confidence to effective weight multiplier (blueprint § confidence gating)."""
    return max(0.3, min(1.0, 0.3 + 0.7 * _clip01(block_confidence)))


def block_confidence_for_factor(
    factor: str,
    confidence: ConfidenceBreakdownV2,
) -> float:
    mapping = {
        "baseline_strength": confidence.teams_confidence,
        "current_form": confidence.teams_confidence,
        "motivation": confidence.teams_confidence,
        "squad_availability": confidence.squads_confidence,
        "coach_factor": confidence.coaches_confidence,
        "schedule_context": confidence.schedule_confidence,
        "h2h_context_bias": confidence.h2h_confidence,
    }
    return mapping.get(factor, confidence.overall_confidence_score)


@dataclass(frozen=True)
class ResolvedWeights:
    phase: SeasonPhase
    weights: Dict[str, float]
    special_overrides_applied: tuple[str, ...] = ()


def apply_confidence_to_weights(
    weights: Mapping[str, float],
    confidence: ConfidenceBreakdownV2,
) -> Dict[str, float]:
    adjusted = {
        k: float(weights[k]) * confidence_factor(block_confidence_for_factor(k, confidence))
        for k in weights
    }
    total = sum(adjusted.values()) or 1.0
    return {k: v / total for k, v in adjusted.items()}


def resolve_team_weights(
    *,
    phase: SeasonPhase,
    confidence: ConfidenceBreakdownV2,
    is_first_match: bool = False,
    is_new_coach_window: bool = False,
    matches_in_charge: Optional[int] = None,
    pre_big_match_risk: float = 0.0,
    post_big_match_risk: float = 0.0,
) -> ResolvedWeights:
    """Phase table + blueprint §18 weight nudges + block-confidence scaling."""
    w = base_weights_for_phase(phase)
    overrides: list[str] = []

    if is_first_match:
        w["coach_factor"] *= 1.35
        w["current_form"] *= 0.85
        w["motivation"] *= 1.10
        overrides.append("new_coach_first_match")
    elif is_new_coach_window or (
        matches_in_charge is not None and 2 <= matches_in_charge <= 4
    ):
        w["coach_factor"] *= 1.15
        w["current_form"] *= 0.90
        overrides.append("new_coach_window")

    if pre_big_match_risk >= 0.25:
        w["schedule_context"] *= 1.20
        overrides.append("pre_big_match")
    if post_big_match_risk >= 0.15:
        w["schedule_context"] *= 1.15
        overrides.append("post_big_match")

    total = sum(w.values()) or 1.0
    w = {k: v / total for k, v in w.items()}
    w = apply_confidence_to_weights(w, confidence)
    return ResolvedWeights(phase=phase, weights=w, special_overrides_applied=tuple(overrides))


def resolve_weights_from_snapshot(snapshot: MatchAnalysisSnapshotV2, *, side: str) -> ResolvedWeights:
    meta = snapshot.match_meta
    phase = resolve_season_phase(
        season_phase=meta.season_phase,
        season_progress=float(meta.season_progress or 0.0),
    )
    coach = snapshot.home_coach if side == "home" else snapshot.away_coach
    schedule = snapshot.home_schedule if side == "home" else snapshot.away_schedule
    return resolve_team_weights(
        phase=phase,
        confidence=snapshot.confidence,
        is_first_match=coach.is_first_match,
        is_new_coach_window=coach.is_new_coach_bounce_window,
        matches_in_charge=coach.matches_in_charge,
        pre_big_match_risk=schedule.pre_big_match_preservation_risk,
        post_big_match_risk=schedule.post_big_match_relaxation_risk,
    )
