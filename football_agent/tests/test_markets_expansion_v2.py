"""First-batch v2 market expansion (8 markets)."""

from __future__ import annotations

from football_agent.domain.enums_v2 import LeagueMarketKey
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.scorers.league_scorer_v2 import (
    BEST_MARKET_MIN_USEFUL_ODDS,
    MARKET_KEYS,
    LeagueScorerV2,
    _best_market_odds_factor,
    _extend_market_probabilities,
)
from football_agent.tests.test_express_builder_v2 import make_prediction
from football_agent.tests.test_scorer_v2 import make_snapshot

SCORER = LeagueScorerV2()
BUILDER = ExpressBuilderV2()


def test_all_eight_market_keys_in_predictions() -> None:
    snap = make_snapshot(home_baseline=0.7, away_baseline=0.5, with_odds=True)
    result = SCORER.score_snapshot(snap)
    keys = {m.market_key for m in result.market_predictions}
    assert keys == set(MARKET_KEYS) == {m.value for m in LeagueMarketKey}


def test_extended_probs_sane() -> None:
    base = {
        "HOME_WIN": 0.55,
        "AWAY_WIN": 0.20,
        "HOME_NOT_LOSE": 0.75,
        "AWAY_NOT_LOSE": 0.45,
        "BTTS_YES": 0.60,
    }
    ext = _extend_market_probabilities(
        base,
        r_home=0.7,
        r_away=0.5,
        avg_goals_home=1.4,
        avg_goals_away=1.0,
        h2h_btts=0.55,
        over25_rate=0.5,
    )
    assert ext["OVER_1_5"] >= ext["BTTS_YES"] - 0.01
    assert ext["HOME_TEAM_TO_SCORE"] >= ext["HOME_WIN"] * 0.7
    assert ext["AWAY_TEAM_TO_SCORE"] <= 0.88


def test_missing_new_odds_does_not_crash() -> None:
    snap = make_snapshot(with_odds=False)
    result = SCORER.score_snapshot(snap)
    over = next(m for m in result.market_predictions if m.market_key == "OVER_1_5")
    assert over.book_odds is None
    assert over.edge is None
    assert over.probability > 0.4


def test_low_odds_downweighted_for_best_market() -> None:
    snap = make_snapshot(home_baseline=0.88, away_baseline=0.4, with_odds=True)
    snap.odds.home_not_lose = snap.odds.home_not_lose.model_copy(update={"odds": 1.08})
    result = SCORER.score_snapshot(snap)
    assert result.best_market.market_key != "HOME_NOT_LOSE" or (
        result.best_market.book_odds is not None and result.best_market.book_odds >= 1.15
    )
    assert _best_market_odds_factor(1.08) < _best_market_odds_factor(BEST_MARKET_MIN_USEFUL_ODDS)


def test_express_still_works_with_expanded_markets() -> None:
    results = [
        make_prediction(i, book_odds=1.35, probability=0.78)
        for i in range(1, 5)
    ]
    bet = BUILDER.build_express(results, target_odds=3.5)
    assert bet is not None
    assert len(bet.events) >= 2


def test_tiny_odds_rejected_for_express() -> None:
    r = make_prediction(1, book_odds=1.10, probability=0.85)
    assert BUILDER.build_express([r], target_odds=2.0) is None
