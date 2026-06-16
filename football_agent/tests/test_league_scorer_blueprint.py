"""LeagueScorerV2 alignment with league_logic_blueprint weights and overrides."""

from __future__ import annotations

from football_agent.domain.enums_v2 import SeasonPhase
from football_agent.domain.models_v2 import ConfidenceBreakdownV2
from football_agent.scorers.league_factor_weights import (
    PHASE_FACTOR_WEIGHTS,
    apply_confidence_to_weights,
    base_weights_for_phase,
    resolve_team_weights,
)
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.tests.test_scorer_v2 import HOME, _coach, _schedule, _team_context, make_snapshot


def _high_confidence() -> ConfidenceBreakdownV2:
    return ConfidenceBreakdownV2(
        match_meta_confidence=0.9,
        teams_confidence=0.9,
        squads_confidence=0.9,
        coaches_confidence=0.9,
        odds_confidence=0.9,
        news_confidence=0.9,
        schedule_confidence=0.9,
        h2h_confidence=0.9,
        overall_confidence_score=0.9,
        overall_completeness_score=0.9,
    )


def test_phase_weights_match_blueprint_table() -> None:
    early = PHASE_FACTOR_WEIGHTS[SeasonPhase.EARLY]
    final = PHASE_FACTOR_WEIGHTS[SeasonPhase.FINAL_RUN_IN]
    assert round(sum(early.values()), 2) == 1.0
    assert early["baseline_strength"] == 0.24
    assert final["motivation"] == 0.28
    assert final["baseline_strength"] < early["baseline_strength"]
    assert final["motivation"] > early["motivation"]


def test_motivation_weight_dominates_late_season_score() -> None:
    scorer = LeagueScorerV2()
    base_kwargs = dict(
        home_baseline=0.5,
        away_baseline=0.5,
        home_form=0.5,
        away_form=0.5,
        home_motivation=0.95,
        away_motivation=0.5,
        confidence=0.9,
        confidence_breakdown=_high_confidence(),
    )
    early = scorer.score_snapshot(
        make_snapshot(season_phase=SeasonPhase.EARLY, **base_kwargs),
    )
    final = scorer.score_snapshot(
        make_snapshot(season_phase=SeasonPhase.FINAL_RUN_IN, **base_kwargs),
    )
    early_gap = (
        early.home_scoring.factor_scores.total_score
        - early.away_scoring.factor_scores.total_score
    )
    final_gap = (
        final.home_scoring.factor_scores.total_score
        - final.away_scoring.factor_scores.total_score
    )
    assert final_gap > early_gap


def test_new_coach_first_match_boosts_coach_and_flags() -> None:
    scorer = LeagueScorerV2()
    normal = scorer.score_snapshot(
        make_snapshot(home_form=0.8, away_form=0.4, confidence=0.7),
    )
    first = scorer.score_snapshot(
        make_snapshot(home_form=0.8, away_form=0.4, home_first_match=True, confidence=0.7),
    )
    assert first.home_scoring.factor_scores.coach_factor > normal.home_scoring.factor_scores.coach_factor
    assert first.home_scoring.factor_scores.current_form <= normal.home_scoring.factor_scores.current_form
    assert "new_coach" in first.home_scoring.summary_flags


def test_new_coach_window_uses_form_under_current_coach() -> None:
    scorer = LeagueScorerV2()
    snap = make_snapshot(confidence=0.7)
    snap.home_team_context.form.last_5_form_score = 0.4
    snap.home_team_context.form.last_10_form_score = 0.4
    snap.home_team_context.form.form_under_current_coach = 0.9
    snap.home_coach = _coach(HOME, bounce_window=True, matches_in_charge=3)
    result = scorer.score_snapshot(snap)
    assert result.home_scoring.factor_scores.current_form > 0.55
    assert "coach_bounce_window" in result.home_scoring.summary_flags


def test_pre_big_match_penalizes_favorite() -> None:
    scorer = LeagueScorerV2()
    baseline = make_snapshot(
        home_baseline=0.88,
        away_baseline=0.42,
        home_form=0.82,
        away_form=0.38,
        confidence=0.7,
    )
    risky = make_snapshot(
        home_baseline=0.88,
        away_baseline=0.42,
        home_form=0.82,
        away_form=0.38,
        confidence=0.7,
    )
    risky.home_schedule = _schedule(
        HOME,
        pre_big=0.35,
        days_to_next=2,
        rotation=0.6,
    )
    base_score = scorer.score_snapshot(baseline).home_scoring.factor_scores.total_score
    risk_score = scorer.score_snapshot(risky).home_scoring.factor_scores.total_score
    assert risk_score < base_score
    assert "pre_big_match_risk" in scorer.score_snapshot(risky).home_scoring.summary_flags


def test_low_squads_confidence_reduces_squad_weight() -> None:
    high_conf = _high_confidence()
    low_squad = high_conf.model_copy(update={"squads_confidence": 0.2})
    w_high = apply_confidence_to_weights(base_weights_for_phase(SeasonPhase.MID), high_conf)
    w_low = apply_confidence_to_weights(base_weights_for_phase(SeasonPhase.MID), low_squad)
    assert w_low["squad_availability"] < w_high["squad_availability"]


def test_resolve_team_weights_marks_special_overrides() -> None:
    resolved = resolve_team_weights(
        phase=SeasonPhase.MID,
        confidence=_high_confidence(),
        is_first_match=True,
        pre_big_match_risk=0.4,
    )
    assert "new_coach_first_match" in resolved.special_overrides_applied
    assert "pre_big_match" in resolved.special_overrides_applied


def test_low_confidence_shrinks_market_probabilities() -> None:
    scorer = LeagueScorerV2()
    high = scorer.score_snapshot(
        make_snapshot(
            home_baseline=0.82,
            away_baseline=0.45,
            confidence=0.75,
            with_odds=True,
        ),
    )
    low = scorer.score_snapshot(
        make_snapshot(
            home_baseline=0.82,
            away_baseline=0.45,
            confidence=0.35,
            with_odds=True,
        ),
    )
    high_home = next(m for m in high.market_predictions if m.market_key == "HOME_WIN")
    low_home = next(m for m in low.market_predictions if m.market_key == "HOME_WIN")
    assert abs(low_home.probability - 0.5) < abs(high_home.probability - 0.5)
