"""Unit tests for ExpressBuilderV2 — no API."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from football_agent.domain.enums_v2 import ExpressSafetyClass
from football_agent.domain.models_v2 import (
    ExpressScreeningV2,
    LeagueFactorScoresV2,
    MarketPredictionV2,
    MatchMetaV2,
    MatchPredictionResultV2,
    TeamRefV2,
    TeamScoringResultV2,
)
from football_agent.express.express_builder_v2 import ExpressBuilderV2

UTC = timezone.utc
BUILDER = ExpressBuilderV2()


def _team(team_id: int, name: str) -> TeamRefV2:
    return TeamRefV2(team_id=team_id, name=name, short_name=name[:4])


def _team_scoring(team: TeamRefV2) -> TeamScoringResultV2:
    return TeamScoringResultV2(team=team, factor_scores=LeagueFactorScoresV2())


def make_prediction(
    match_id: int,
    *,
    probability: float = 0.75,
    book_odds: float = 1.35,
    allow_for_express: bool = True,
    market_key: str = "HOME_NOT_LOSE",
    confidence: float = 0.7,
) -> MatchPredictionResultV2:
    home = _team(1, "Home FC")
    away = _team(2, "Away FC")
    meta = MatchMetaV2(
        match_id=match_id,
        season=2024,
        competition_name="Test League",
        competition_code="PL",
        match_date_utc=datetime(2024, 4, 25, 12 + match_id % 8, 0, tzinfo=UTC),
        home_team=home,
        away_team=away,
    )
    market = MarketPredictionV2(
        market_key=market_key,
        probability=probability,
        book_odds=book_odds,
        label=market_key,
    )
    return MatchPredictionResultV2(
        match_meta=meta,
        home_scoring=_team_scoring(home),
        away_scoring=_team_scoring(away),
        best_market=market,
        express_safety=ExpressScreeningV2(
            safety_class=ExpressSafetyClass.EXPRESS_SAFE if allow_for_express else ExpressSafetyClass.EXPRESS_AVOID,
            allow_for_express=allow_for_express,
        ),
        overall_confidence_score=confidence,
    )


def test_builds_express_from_safe_matches() -> None:
    results = [
        make_prediction(1, book_odds=1.30, probability=0.78),
        make_prediction(2, book_odds=1.40, probability=0.76),
        make_prediction(3, book_odds=1.55, probability=0.74),
    ]
    bet = BUILDER.build_express(results, target_odds=3.0, max_events=5)
    assert bet is not None
    assert len(bet.events) >= 2
    assert bet.total_odds > 1.0
    assert 0.0 < bet.total_probability <= 1.0
    expected_odds = 1.0
    expected_prob = 1.0
    for ev in bet.events:
        expected_odds *= ev.book_odds
        expected_prob *= ev.probability
    assert abs(bet.total_odds - round(expected_odds, 2)) < 0.02
    assert abs(bet.total_probability - round(expected_prob, 4)) < 0.0001


def test_returns_none_without_candidates() -> None:
    results = [
        make_prediction(1, allow_for_express=False),
        make_prediction(2, book_odds=None),  # type: ignore[arg-type]
    ]
    results[1].best_market.book_odds = None
    assert BUILDER.build_express(results, target_odds=3.0) is None


def test_rejects_not_allowed_for_express() -> None:
    results = [make_prediction(1, allow_for_express=False, book_odds=1.30)]
    assert BUILDER.build_express(results, target_odds=2.0) is None


def test_rejects_missing_odds() -> None:
    r = make_prediction(1)
    r.best_market.book_odds = None
    assert BUILDER.build_express([r], target_odds=2.0) is None


def test_respects_max_events() -> None:
    results = [
        make_prediction(i, book_odds=1.25, probability=0.80)
        for i in range(1, 8)
    ]
    bet = BUILDER.build_express(results, target_odds=10.0, max_events=3)
    assert bet is not None
    assert len(bet.events) <= 3


def test_no_duplicate_match_ids() -> None:
    results = [
        make_prediction(1, book_odds=1.30),
        make_prediction(1, book_odds=1.35),
        make_prediction(2, book_odds=1.40),
    ]
    bet = BUILDER.build_express(results, target_odds=2.0)
    assert bet is not None
    ids = [e.match_meta.match_id for e in bet.events]
    assert len(ids) == len(set(ids))


def test_skips_broken_result_fail_soft() -> None:
    good = make_prediction(1, book_odds=1.30)
    bad = make_prediction(2, book_odds=1.40)
    bad.best_market = None  # type: ignore[assignment]
    bet = BUILDER.build_express([good, bad], target_odds=1.5, max_events=2)
    assert bet is not None
    assert len(bet.events) == 1
