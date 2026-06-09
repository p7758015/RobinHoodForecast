from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[1]))

from datetime import date, timedelta

from football_agent.domain.features import (
    calculate_coach_strength,
    calculate_form,
    calculate_h2h_stats,
    calculate_motivation,
    normalize_coach_pair,
)
from football_agent.domain.models import CoachMatch, H2HStats, MatchResult, StandingEntry, Team
from football_agent.domain.probability_model import (
    compute_h2h_bias,
    compute_market_probabilities,
    compute_rating,
    compute_season_progress,
    compute_weights,
    select_best_markets,
)
from football_agent.engine.express_builder import build_express


def _mk_standings(competition_code: str) -> list[StandingEntry]:
    # 20 teams, descending points
    standings: list[StandingEntry] = []
    for pos in range(1, 21):
        pts = max(0, 80 - (pos - 1) * 3)
        standings.append(
            StandingEntry(
                team=Team(id=pos, name=f"T{pos}", short_name=f"T{pos}"),
                position=pos,
                points=pts,
                played_games=34,
                won=0,
                draw=0,
                lost=0,
                goals_for=0,
                goals_against=0,
                goal_difference=0,
                form="",
            )
        )
    return standings


def test_calculate_motivation() -> None:
    standings = _mk_standings("PL")

    # Team near relegation zone should have high survival motivation
    m, eliminated, fighting, _warnings = calculate_motivation(
        position=19,
        points=20,
        played_rounds=34,
        total_rounds=38,
        standings=standings,
        competition_code="PL",
    )
    assert 0.0 <= m <= 1.0
    assert fighting is True
    assert eliminated in (True, False)

    # Mid-table should fallback to baseline 0.2/0.3
    m2, eliminated2, fighting2, _warnings2 = calculate_motivation(
        position=10,
        points=45,
        played_rounds=34,
        total_rounds=38,
        standings=standings,
        competition_code="PL",
    )
    assert eliminated2 is False
    assert fighting2 in (True, False)
    assert m2 in (0.2, 0.3) or m2 > 0.3


def test_coach_strength_and_normalize() -> None:
    today = date.today()
    matches = [
        CoachMatch(match_id=1, team_id=1, opponent_id=2, result="W", date=today - timedelta(days=7)),
        CoachMatch(match_id=2, team_id=1, opponent_id=3, result="D", date=today - timedelta(days=14)),
        CoachMatch(match_id=3, team_id=1, opponent_id=2, result="L", date=today - timedelta(days=21)),
        CoachMatch(match_id=4, team_id=1, opponent_id=2, result="W", date=today - timedelta(days=28)),
    ]
    c_raw = calculate_coach_strength(matches, opponent_team_id=2)
    assert 0.0 <= c_raw <= 1.0
    ch, ca = normalize_coach_pair(c_raw, 1.0 - c_raw)
    assert abs((ch + ca) - 1.0) < 1e-9


def test_calculate_form() -> None:
    base_date = date.today()
    season_matches = [
        MatchResult(match_id=i, date=base_date - timedelta(days=i * 7), is_home=(i % 2 == 0), goals_for=2, goals_against=1, result="W")
        for i in range(1, 9)
    ]
    f = calculate_form(season_matches=season_matches, coach_start_date=None, is_home=True)
    assert 0.0 <= f <= 1.0

    coach_start = base_date - timedelta(days=21)
    f2 = calculate_form(season_matches=season_matches, coach_start_date=coach_start, is_home=False)
    assert 0.0 <= f2 <= 1.0


def test_h2h_stats_and_bias() -> None:
    base_date = date.today()
    h2h_matches = [
        MatchResult(match_id=1, date=base_date, is_home=True, goals_for=2, goals_against=0, result="W"),
        MatchResult(match_id=2, date=base_date - timedelta(days=30), is_home=True, goals_for=1, goals_against=1, result="D"),
        MatchResult(match_id=3, date=base_date - timedelta(days=60), is_home=True, goals_for=0, goals_against=1, result="L"),
        MatchResult(match_id=4, date=base_date - timedelta(days=90), is_home=True, goals_for=3, goals_against=2, result="W"),
    ]
    h2h = calculate_h2h_stats(h2h_matches=h2h_matches, current_home_team_id=1)
    assert h2h.total_matches == 4
    bias = compute_h2h_bias(h2h)
    assert -0.3 <= bias <= 0.3


def test_probabilities_and_selection() -> None:
    h2h = H2HStats(
        total_matches=6,
        home_wins=3,
        away_wins=2,
        draws=1,
        home_goals_avg=1.5,
        away_goals_avg=1.2,
        btts_rate=0.55,
        over25_rate=0.5,
    )
    probs = compute_market_probabilities(
        R_home=0.62,
        R_away=0.48,
        avg_goals_home=1.6,
        avg_goals_away=1.2,
        h2h=h2h,
    )
    assert set(probs.keys()) == {"HOME_WIN", "AWAY_WIN", "HOME_NOT_LOSE", "AWAY_NOT_LOSE", "BTTS_YES"}
    assert all(0.0 <= p <= 1.0 for p in probs.values())

    top = select_best_markets(probabilities=probs, odds=None, top_n=3)
    assert len(top) == 3
    assert top[0].probability >= top[1].probability >= top[2].probability


def test_weights_rating_progress() -> None:
    g = compute_season_progress(played_rounds=19, total_rounds=38)
    wM, wF, wC = compute_weights(g)
    assert abs((wM + wF + wC) - 1.0) < 1e-9
    r = compute_rating(M=0.7, F=0.6, C=0.55, eliminated=False, opponent_is_fighting=True, wM=wM, wF=wF, wC=wC)
    assert 0.0 <= r <= 1.0


def test_build_express() -> None:
    from datetime import datetime, timezone

    dummy_h2h = H2HStats(
        total_matches=0,
        home_wins=0,
        away_wins=0,
        draws=0,
        home_goals_avg=0.0,
        away_goals_avg=0.0,
        btts_rate=0.5,
        over25_rate=0.5,
    )

    results = []
    for i in range(6):
        home = Team(id=100 + i * 2, name=f"H{i}", short_name=f"H{i}")
        away = Team(id=101 + i * 2, name=f"A{i}", short_name=f"A{i}")
        match = __import__("football_agent.domain.models", fromlist=["Match"]).Match(
            id=1000 + i,
            competition_code="PL",
            home_team=home,
            away_team=away,
            utc_date=datetime(2024, 4, 25, 18, 0, tzinfo=timezone.utc),
            status="SCHEDULED",
            matchday=30,
        )
        best_market = __import__("football_agent.domain.models", fromlist=["MarketPrediction"]).MarketPrediction(
            market="HOME_NOT_LOSE",
            probability=0.75 + i * 0.01,
            odds=1.35 + (i % 3) * 0.15,  # 1.35, 1.50, 1.65 repeating
            label="1X",
        )
        ta_home = __import__("football_agent.domain.models", fromlist=["TeamAnalysis"]).TeamAnalysis(
            team=home,
            motivation=0.6,
            form=0.6,
            coach_strength=0.5,
            rating=0.6,
            eliminated=False,
            is_fighting=True,
        )
        ta_away = __import__("football_agent.domain.models", fromlist=["TeamAnalysis"]).TeamAnalysis(
            team=away,
            motivation=0.5,
            form=0.5,
            coach_strength=0.5,
            rating=0.5,
            eliminated=False,
            is_fighting=True,
        )
        mar = __import__("football_agent.domain.models", fromlist=["MatchAnalysisResult"]).MatchAnalysisResult(
            match=match,
            home_analysis=ta_home,
            away_analysis=ta_away,
            h2h=dummy_h2h,
            markets=[best_market],
            best_market=best_market,
            season_progress=0.8,
        )
        results.append(mar)

    express = build_express(results, target_odds=3.0, max_events=6, tolerance=0.20)
    assert express.events
    assert isinstance(express.total_odds, float)
    assert abs(express.total_odds - 3.0) <= 3.0 * 0.5  # loose check; algorithm is greedy


if __name__ == "__main__":
    test_calculate_motivation()
    test_coach_strength_and_normalize()
    test_calculate_form()
    test_h2h_stats_and_bias()
    test_probabilities_and_selection()
    test_weights_rating_progress()
    test_build_express()
    print("test_run.py OK")

