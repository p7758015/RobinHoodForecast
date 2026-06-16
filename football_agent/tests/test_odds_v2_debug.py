"""Odds flow, output formatting, best_market balance (v2 debug pass)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from football_agent.data_providers.api_football_client import (
    ApiFootballClient,
    _parse_bookmaker_bets,
    _parse_double_chance,
)
from football_agent.data_providers.odds_utils import infer_api_football_season, merge_odds
from football_agent.domain.models import Odds
from football_agent.domain.models_v2 import MarketPredictionV2
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder
from football_agent.output.market_display import format_market_pick
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2, _book_odds_map
from football_agent.tests.test_scorer_v2 import make_snapshot

UTC = timezone.utc


def test_infer_api_football_season_may_2026() -> None:
    assert infer_api_football_season(datetime(2026, 5, 24, tzinfo=UTC)) == 2025


def test_parse_double_chance_1x_x2_aliases() -> None:
    bets = {
        "Double Chance": {
            "values": [
                {"value": "1X", "odd": "1.22"},
                {"value": "X2", "odd": "1.88"},
            ]
        }
    }
    h, a = _parse_double_chance(bets)
    assert h == 1.22
    assert a == 1.88


def test_parse_btts_bet_name_variants() -> None:
    bets = [
        {
            "name": "Both Teams To Score",
            "values": [{"value": "Yes", "odd": "1.70"}],
        }
    ]
    o = _parse_bookmaker_bets(bets, fixture_id=99)
    assert o.btts_yes == 1.70


def test_merge_bookmakers_fills_gaps() -> None:
    a = Odds(fixture_id=1, home_win=2.1)
    b = Odds(fixture_id=1, btts_yes=1.65, over_15=1.25)
    merged = merge_odds(a, b)
    assert merged.home_win == 2.1
    assert merged.btts_yes == 1.65
    assert merged.over_15 == 1.25


def test_snapshot_odds_map_to_book_odds() -> None:
    snap = make_snapshot(with_odds=True)
    book = _book_odds_map(snap.odds)
    assert book["HOME_WIN"] == 1.55
    assert book["HOME_NOT_LOSE"] == 1.18

    result = LeagueScorerV2().score_snapshot(snap)
    by_key = {m.market_key: m for m in result.market_predictions}
    assert by_key["HOME_WIN"].book_odds == 1.55
    assert by_key["AWAY_NOT_LOSE"].book_odds == 1.95


def test_build_odds_context_from_domain_odds() -> None:
    raw = Odds(
        fixture_id=1,
        home_win=1.9,
        away_win=4.2,
        home_not_lose=1.3,
        btts_yes=1.8,
    )
    ctx = MatchSnapshotBuilder._build_odds_context(raw)
    assert ctx.home_win is not None
    assert ctx.home_win.odds == 1.9
    assert _book_odds_map(ctx)["BTTS_YES"] == 1.8


def test_format_market_pick_clean_without_odds() -> None:
    line = format_market_pick("HOME_WIN", 0.72, None, label="П1")
    assert "П1" in line
    assert "кф н/д" in line
    assert "72%" in line
    assert ",  ," not in line
    assert "кф —" not in line


def test_format_market_pick_with_odds() -> None:
    line = format_market_pick("HOME_NOT_LOSE", 0.74, 1.42, label="1X")
    assert line == "1X, кф 1.42, 74%"


def test_best_market_prefers_actionable_over_short_dc() -> None:
    scorer = LeagueScorerV2()
    markets = [
        MarketPredictionV2(
            market_key="HOME_NOT_LOSE",
            probability=0.90,
            book_odds=1.10,
            edge=0.01,
            label="1X",
        ),
        MarketPredictionV2(
            market_key="HOME_WIN",
            probability=0.58,
            book_odds=1.72,
            edge=0.09,
            label="П1",
        ),
        MarketPredictionV2(
            market_key="OVER_1_5",
            probability=0.82,
            book_odds=1.35,
            edge=0.04,
            label="ТБ 1.5",
        ),
    ]
    best = scorer._pick_best_market(markets, confidence=0.55)
    assert best.market_key in ("HOME_WIN", "OVER_1_5")


def test_express_builder_survives_partial_book_odds() -> None:
    snap = make_snapshot(with_odds=True, home_baseline=0.7, away_baseline=0.4)
    pred = LeagueScorerV2().score_snapshot(snap)
    pred.match_meta = pred.match_meta.model_copy(update={"competition_code": "PL"})
    pred.express_safety.allow_for_express = True
    builder = ExpressBuilderV2()
    result = builder.build_express([pred], target_odds=2.5)
    assert result is not None


def test_find_fixture_tries_multiple_seasons() -> None:
    client = ApiFootballClient(api_key="test")
    client.get_fixtures = MagicMock(side_effect=lambda league_id, date_str, season=None: (
        [{"teams": {"home": {"name": "Tottenham"}, "away": {"name": "Everton"}}, "fixture": {"id": 555}}]
        if season == 2025
        else []
    ))
    fid = client.find_fixture_id(
        "Tottenham Hotspur",
        "Everton",
        "2026-05-24",
        league_id=39,
        season=2024,
        seasons=[2025, 2024],
    )
    assert fid == 555
    assert client.get_fixtures.call_count >= 1
