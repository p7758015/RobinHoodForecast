"""Local sanity tests for LeagueScorerV2 — no API, fixture snapshots only."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import pytest

from football_agent.domain.enums_v2 import ExpressSafetyClass, LeagueMarketKey, SeasonPhase
from football_agent.domain.models_v2 import (
    CoachContextV2,
    CoachRefV2,
    ConfidenceBreakdownV2,
    H2HContextV2,
    MatchAnalysisSnapshotV2,
    MatchMetaV2,
    MatchPredictionResultV2,
    OddsContextV2,
    OddsMarketV2,
    ScheduleContextV2,
    SquadContextV2,
    TeamContextV2,
    TeamFormBlockV2,
    TeamMotivationBlockV2,
    TeamRefV2,
    TeamScheduleMiniBlockV2,
)
from football_agent.scorers.league_scorer_v2 import MARKET_KEYS, LeagueScorerV2

UTC = timezone.utc
HOME = TeamRefV2(team_id=1, name="Home FC", short_name="Home")
AWAY = TeamRefV2(team_id=2, name="Away FC", short_name="Away")


def _team_context(
    team: TeamRefV2,
    *,
    baseline: float = 0.5,
    form: float = 0.5,
    motivation: float = 0.5,
) -> TeamContextV2:
    form_block = TeamFormBlockV2(
        last_5_form_score=form,
        last_10_form_score=form,
        home_form_score=form,
        away_form_score=form,
        form_under_current_coach=form,
    )
    return TeamContextV2(
        team=team,
        baseline_strength_score=baseline,
        form=form_block,
        motivation=TeamMotivationBlockV2(motivation_score=motivation),
        schedule=TeamScheduleMiniBlockV2(),
    )


def _squad(team: TeamRefV2, *, xi_confidence: float = 0.5) -> SquadContextV2:
    return SquadContextV2(team=team, starting_xi_confidence=xi_confidence)


def _coach(
    team: TeamRefV2,
    *,
    first_match: bool = False,
    bounce_window: bool = False,
    matches_in_charge: Optional[int] = None,
    global_strength: float = 0.55,
) -> CoachContextV2:
    return CoachContextV2(
        coach=CoachRefV2(name=f"Coach {team.team_id}"),
        team=team,
        is_first_match=first_match,
        is_new_coach_bounce_window=bounce_window,
        matches_in_charge=matches_in_charge,
        coach_global_strength_score=global_strength,
        coach_vs_opponent_team_score=global_strength,
        coach_vs_opponent_coach_score=0.5,
    )


def _schedule(
    team: TeamRefV2,
    *,
    rotation: float = 0.1,
    congestion: float = 0.1,
    pre_big: float = 0.0,
    post_big: float = 0.0,
    days_to_next: Optional[int] = None,
    days_since_last: Optional[int] = None,
) -> ScheduleContextV2:
    return ScheduleContextV2(
        team=team,
        rotation_risk_score=rotation,
        fixture_congestion_score=congestion,
        pre_big_match_preservation_risk=pre_big,
        post_big_match_relaxation_risk=post_big,
        days_to_next_match=days_to_next,
        days_since_last_match=days_since_last,
    )


def _odds_market(key: str, price: float) -> OddsMarketV2:
    return OddsMarketV2(
        market_key=key,
        market_name=key,
        selection_name=key,
        odds=price,
    )


def make_snapshot(
    *,
    home_baseline: float = 0.5,
    away_baseline: float = 0.5,
    home_form: float = 0.5,
    away_form: float = 0.5,
    home_motivation: float = 0.5,
    away_motivation: float = 0.5,
    confidence: float = 0.55,
    h2h_bias: float = 0.0,
    h2h_matches: int = 0,
    h2h_recent_score: float = 0.0,
    home_xi: float = 0.5,
    away_xi: float = 0.5,
    home_first_match: bool = False,
    away_first_match: bool = False,
    home_rotation: float = 0.1,
    away_rotation: float = 0.1,
    with_odds: bool = False,
    season_progress: float = 0.75,
    season_phase: Optional[SeasonPhase] = None,
    confidence_breakdown: Optional[ConfidenceBreakdownV2] = None,
) -> MatchAnalysisSnapshotV2:
    meta = MatchMetaV2(
        match_id=1000 + int(home_baseline * 100) + int(away_baseline * 10),
        season=2024,
        competition_name="Premier League",
        competition_code="PL",
        match_date_utc=datetime(2024, 4, 25, 15, 0, tzinfo=UTC),
        home_team=HOME,
        away_team=AWAY,
        season_progress=season_progress,
        season_phase=season_phase,
    )
    odds = OddsContextV2()
    if with_odds:
        odds = OddsContextV2(
            home_win=_odds_market("HOME_WIN", 1.55),
            away_win=_odds_market("AWAY_WIN", 2.80),
            home_not_lose=_odds_market("HOME_NOT_LOSE", 1.18),
            away_not_lose=_odds_market("AWAY_NOT_LOSE", 1.95),
            btts_yes=_odds_market("BTTS_YES", 1.72),
            odds_confidence=0.8,
        )
    return MatchAnalysisSnapshotV2(
        match_meta=meta,
        home_team_context=_team_context(
            HOME, baseline=home_baseline, form=home_form, motivation=home_motivation
        ),
        away_team_context=_team_context(
            AWAY, baseline=away_baseline, form=away_form, motivation=away_motivation
        ),
        home_squad=_squad(HOME, xi_confidence=home_xi),
        away_squad=_squad(AWAY, xi_confidence=away_xi),
        home_coach=_coach(HOME, first_match=home_first_match),
        away_coach=_coach(AWAY, first_match=away_first_match),
        home_schedule=_schedule(HOME, rotation=home_rotation),
        away_schedule=_schedule(AWAY, rotation=away_rotation),
        odds=odds,
        h2h_context=H2HContextV2(
            team_h2h_total_matches=h2h_matches,
            team_h2h_recent_score=h2h_recent_score,
            h2h_context_bias=h2h_bias,
            h2h_btts_rate=0.55,
        ),
        confidence=confidence_breakdown
        or ConfidenceBreakdownV2(overall_confidence_score=confidence),
    )


@pytest.fixture
def scorer() -> LeagueScorerV2:
    return LeagueScorerV2()


def _market_map(result: MatchPredictionResultV2) -> dict:
    return {m.market_key: m for m in result.market_predictions}


def assert_valid_markets(result: MatchPredictionResultV2) -> None:
    assert result.best_market is not None
    keys = {m.market_key for m in result.market_predictions}
    assert result.best_market.market_key in keys
    for m in result.market_predictions:
        assert m.probability is not None
        assert not math.isnan(m.probability)
        assert 0.0 <= m.probability <= 1.0
        if m.fair_odds is not None:
            assert m.fair_odds > 1.0
            assert abs(m.fair_odds - round(1.0 / m.probability, 2)) < 0.02
        if m.book_odds is None:
            assert m.edge is None
        else:
            assert m.edge is not None
    home = _market_map(result)["HOME_WIN"]
    hnl = _market_map(result)["HOME_NOT_LOSE"]
    away = _market_map(result)["AWAY_WIN"]
    anl = _market_map(result)["AWAY_NOT_LOSE"]
    assert hnl.probability >= home.probability - 1e-6
    assert anl.probability >= away.probability - 1e-6


class TestBalancedMatchNoOdds:
    def test_balanced_medium_confidence(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(confidence=0.55, with_odds=False)
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        m = _market_map(result)
        assert m["HOME_NOT_LOSE"].probability <= 0.92
        assert m["AWAY_NOT_LOSE"].probability <= 0.92
        assert result.express_safety.allow_for_express is False or (
            result.express_safety.safety_class != ExpressSafetyClass.EXPRESS_SAFE
        )


class TestStrongHomeFavorite:
    def test_home_markets_dominant(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            home_baseline=0.88,
            away_baseline=0.42,
            home_form=0.82,
            away_form=0.38,
            home_motivation=0.80,
            away_motivation=0.40,
            confidence=0.62,
            home_xi=0.75,
            away_xi=0.70,
            with_odds=True,
        )
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        m = _market_map(result)
        assert m["HOME_WIN"].probability > m["AWAY_WIN"].probability
        assert m["HOME_NOT_LOSE"].probability > m["AWAY_NOT_LOSE"].probability
        assert result.best_market.market_key in ("HOME_WIN", "HOME_NOT_LOSE")
        assert result.express_safety.penalty_score < 0.45


class TestLowConfidence:
    def test_conservative_vs_high_confidence(self, scorer: LeagueScorerV2) -> None:
        low_snap = make_snapshot(
            home_baseline=0.72,
            away_baseline=0.48,
            confidence=0.32,
            with_odds=True,
        )
        high_snap = make_snapshot(
            home_baseline=0.72,
            away_baseline=0.48,
            confidence=0.70,
            with_odds=True,
        )
        low = scorer.score_snapshot(low_snap)
        high = scorer.score_snapshot(high_snap)
        assert low.express_safety.penalty_score >= high.express_safety.penalty_score
        assert low.express_safety.allow_for_express is False
        assert "low_snapshot_confidence" in low.express_safety.reasons


class TestMissingOdds:
    def test_no_odds_still_valid(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(home_baseline=0.7, away_baseline=0.5, confidence=0.6)
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        assert all(m.book_odds is None for m in result.market_predictions)
        assert all(m.edge is None for m in result.market_predictions)
        assert "no_book_odds" in result.express_safety.reasons


class TestNewCoachScheduleVolatility:
    def test_express_penalized_and_flags(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            home_first_match=True,
            home_rotation=0.7,
            away_rotation=0.65,
            confidence=0.55,
        )
        result = scorer.score_snapshot(snap)
        assert "new_coach" in result.home_scoring.summary_flags
        assert "schedule_risk" in result.home_scoring.summary_flags
        assert result.express_safety.penalty_score >= 0.15
        assert "new_coach_first_match" in result.express_safety.reasons
        assert "schedule_volatility" in result.express_safety.reasons
        assert result.express_safety.safety_class != ExpressSafetyClass.EXPRESS_SAFE


class TestStrongH2HSkewBalanced:
    def test_h2h_not_extreme_probs(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            h2h_matches=8,
            h2h_bias=0.25,
            h2h_recent_score=0.6,
            confidence=0.58,
        )
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        m = _market_map(result)
        assert m["HOME_WIN"].probability < 0.85
        assert m["HOME_NOT_LOSE"].probability < 0.96
        assert "strong_h2h_skew" in result.express_safety.reasons or result.express_safety.penalty_score >= 0.08


class TestEmptySquadBlocks:
    def test_low_xi_penalties_not_crash(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(home_xi=0.2, away_xi=0.2, confidence=0.5)
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        assert "thin_squad" in result.home_scoring.summary_flags
        assert "uncertain_lineups" in result.express_safety.reasons


class TestBestMarketConsistency:
    def test_best_market_in_list_and_numeric_sanity(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(with_odds=True, confidence=0.65)
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        assert set(_market_map(result)) == set(MARKET_KEYS) == {m.value for m in LeagueMarketKey}


class TestFailSoftMinimalData:
    def test_minimal_coach_and_h2h(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(confidence=0.4, h2h_matches=0)
        snap.home_coach = CoachContextV2(
            coach=CoachRefV2(name="Unknown"),
            team=HOME,
            coach_global_strength_score=0.5,
        )
        snap.away_coach = CoachContextV2(
            coach=CoachRefV2(name="Unknown"),
            team=AWAY,
            coach_global_strength_score=0.5,
        )
        result = scorer.score_snapshot(snap)
        assert isinstance(result, MatchPredictionResultV2)


class TestExpressSafeMatch:
    def test_clear_favorite_can_allow_express(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            home_baseline=0.90,
            away_baseline=0.35,
            home_form=0.85,
            away_form=0.30,
            home_motivation=0.85,
            away_motivation=0.35,
            confidence=0.72,
            home_xi=0.80,
            away_xi=0.78,
            with_odds=True,
            home_rotation=0.05,
            away_rotation=0.05,
        )
        result = scorer.score_snapshot(snap)
        assert_valid_markets(result)
        m = _market_map(result)
        assert m["HOME_NOT_LOSE"].probability >= 0.68
        assert result.express_safety.penalty_score < 0.22
        assert result.express_safety.allow_for_express is True


class TestBestMarketNeverBttsWhenResultClear:
    def test_btts_not_best_when_home_favorite(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            home_baseline=0.88,
            away_baseline=0.42,
            home_form=0.82,
            away_form=0.38,
            confidence=0.62,
            with_odds=True,
        )
        result = scorer.score_snapshot(snap)
        assert result.best_market.market_key != "BTTS_YES"


class TestBestMarketNotAlwaysDoubleChance:
    def test_strong_favorite_with_odds_may_pick_home_win(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(
            home_baseline=0.92,
            away_baseline=0.30,
            home_form=0.90,
            away_form=0.25,
            confidence=0.75,
            with_odds=True,
            home_xi=0.85,
            away_xi=0.80,
        )
        result = scorer.score_snapshot(snap)
        raw_probs = {m.market_key: m.probability for m in result.market_predictions}
        max_prob_key = max(raw_probs, key=raw_probs.get)
        if max_prob_key == "HOME_WIN" and raw_probs["HOME_WIN"] >= 0.62:
            assert result.best_market.market_key in ("HOME_WIN", "HOME_NOT_LOSE")


class TestNeutralSnapshotFlags:
    def test_no_thin_squad_on_defaults(self, scorer: LeagueScorerV2) -> None:
        snap = make_snapshot(confidence=0.55, home_xi=0.55, away_xi=0.55)
        result = scorer.score_snapshot(snap)
        assert "thin_squad" not in result.home_scoring.summary_flags
        assert "thin_squad" not in result.away_scoring.summary_flags
