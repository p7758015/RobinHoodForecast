"""Phase Evaluation A — express parlay candidate split tests."""

from __future__ import annotations

from datetime import datetime, timezone

from football_agent.domain.enums_v2 import ExpressSafetyClass
from football_agent.domain.models_v2 import (
    ExpressScreeningV2,
    MarketPredictionV2,
    MatchMetaV2,
    MatchPredictionResultV2,
    TeamRefV2,
    TeamScoringResultV2,
)
from football_agent.express.parlay_candidates import split_parlay_candidates
from football_agent.odds.models import (
    MatchOddsContext,
    OddsMarketQuote,
    OddsMarketsBlock,
    OddsMeta,
    OddsProvenance,
)
from football_agent.tests.test_odds_coverage import _full_odds_context


def _team(tid: int, name: str) -> TeamRefV2:
    return TeamRefV2(team_id=tid, name=name, short_name=name)


def _result(
    mid: int,
    home: str,
    away: str,
    *,
    book_odds: float | None,
    prob: float = 0.8,
) -> MatchPredictionResultV2:
    meta = MatchMetaV2(
        match_id=mid,
        season=2026,
        competition_name="Test",
        competition_code="TST",
        match_date_utc=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        home_team=_team(mid * 10, home),
        away_team=_team(mid * 10 + 1, away),
    )
    market = MarketPredictionV2(
        market_key="HOME_WIN",
        probability=prob,
        book_odds=book_odds,
        label="P1",
    )
    return MatchPredictionResultV2(
        match_meta=meta,
        home_scoring=TeamScoringResultV2(team=meta.home_team),
        away_scoring=TeamScoringResultV2(team=meta.away_team),
        market_predictions=[market],
        best_market=market,
        express_safety=ExpressScreeningV2(
            safety_class=ExpressSafetyClass.EXPRESS_SAFE,
            allow_for_express=True,
        ),
    )


def test_split_pricing_vs_no_odds() -> None:
    with_odds = _result(1, "Mexico", "South Africa", book_odds=1.4)
    without = _result(2, "TeamA", "TeamB", book_odds=None, prob=0.85)
    ctx = _full_odds_context()
    split = split_parlay_candidates(
        [with_odds, without],
        min_probability=0.72,
        odds_context_by_match_id={"1": ctx},
    )
    assert len(split.pricing_candidates) == 1
    assert split.pricing_candidates[0].home_team == "Mexico"
    assert split.pricing_candidates[0].has_odds is True
    assert split.pricing_candidates[0].suitable_for_pricing is True

    assert len(split.no_odds_candidates) == 1
    assert split.no_odds_candidates[0].has_odds is False
    assert split.no_odds_candidates[0].note == "odds_unavailable"
    assert split.no_odds_candidates[0].recommended_by_model is True


def test_no_odds_sorted_by_probability() -> None:
    low = _result(3, "L1", "L2", book_odds=None, prob=0.73)
    high = _result(4, "H1", "H2", book_odds=None, prob=0.91)
    split = split_parlay_candidates([low, high], min_probability=0.72)
    assert split.no_odds_candidates[0].probability >= split.no_odds_candidates[1].probability


def test_derived_excluded_when_flag_off() -> None:
    result = _result(5, "X", "Y", book_odds=None, prob=0.8)
    ctx = _full_odds_context()
    split = split_parlay_candidates(
        [result],
        min_probability=0.72,
        include_derived_pricing=False,
        odds_context_by_match_id={"5": ctx},
    )
    # HOME_WIN has book odds on context — should still price
    assert len(split.pricing_candidates) == 1
