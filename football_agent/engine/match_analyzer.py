from __future__ import annotations

import logging
from typing import Dict, List, Optional

from football_agent.config import LEAGUE_IDS_API_FOOTBALL, TOTAL_ROUNDS, CURRENT_SEASON
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.features import (
    calculate_coach_strength,
    calculate_form,
    calculate_h2h_stats,
    calculate_motivation,
    normalize_coach_pair,
)
from football_agent.domain.models import Match, MatchAnalysisResult, TeamAnalysis
from football_agent.domain.probability_model import (
    compute_market_probabilities,
    compute_rating,
    compute_season_progress,
    compute_weights,
    select_best_markets,
)

logger = logging.getLogger(__name__)


def _avg_goals_for(season_matches) -> float:
    if not season_matches:
        return 1.0
    n = len(season_matches)
    if n == 0:
        return 1.0
    return sum(m.goals_for for m in season_matches) / n


def analyze_matches_for_date(
    date_str: str,
    fd_client: FootballDataClient,
    af_client: ApiFootballClient,
) -> List[MatchAnalysisResult]:
    # 1. matches = fd_client.get_matches_by_date(date_str)
    matches = fd_client.get_matches_by_date(date_str)

    # 2. Сгруппировать матчи по competition_code
    by_competition: Dict[str, List[Match]] = {}
    for m in matches:
        by_competition.setdefault(m.competition_code, []).append(m)

    results: List[MatchAnalysisResult] = []

    # 3-6. На лигу один раз: standings, played_rounds, g, weights
    for code, league_matches in by_competition.items():
        standings = fd_client.get_standings(code)
        if not standings:
            logger.warning("No standings for %s", code)
            continue

        # 4. played_rounds = max(s.played_games for s in standings)
        played_rounds = max(s.played_games for s in standings)

        # 5. g = compute_season_progress(...)
        g = compute_season_progress(played_rounds, TOTAL_ROUNDS[code])

        # 6. wM, wF, wC = compute_weights(g)
        wM, wF, wC = compute_weights(g)

        # Map for quick lookup
        standing_by_team_id = {s.team.id: s for s in standings}

        # 7. Для каждого матча
        for match in league_matches:
            try:
                home_id = match.home_team.id
                away_id = match.away_team.id

                home_entry = standing_by_team_id.get(home_id)
                away_entry = standing_by_team_id.get(away_id)
                if not home_entry or not away_entry:
                    logger.warning("Standings missing team for match %s (%s vs %s)", match.id, home_id, away_id)
                    continue

                # Матчи сезона
                home_season_matches = fd_client.get_team_matches_season(home_id, CURRENT_SEASON)
                away_season_matches = fd_client.get_team_matches_season(away_id, CURRENT_SEASON)

                # Тренеры
                home_coach_id, home_coach_name, home_coach_start = fd_client.get_team_coach(home_id)
                away_coach_id, away_coach_name, away_coach_start = fd_client.get_team_coach(away_id)

                home_coach_matches = fd_client.get_coach_matches(home_coach_id) if home_coach_id else []
                away_coach_matches = fd_client.get_coach_matches(away_coach_id) if away_coach_id else []

                # Avg goals
                avg_gf_home = _avg_goals_for(home_season_matches)
                avg_gf_away = _avg_goals_for(away_season_matches)

                # H2H
                h2h_matches = fd_client.get_h2h_matches(home_id, away_id, CURRENT_SEASON)
                h2h_stats = calculate_h2h_stats(h2h_matches, home_id)

                # Motivation
                M_h, elim_h, fight_h = calculate_motivation(
                    position=home_entry.position,
                    points=home_entry.points,
                    played_rounds=played_rounds,
                    total_rounds=TOTAL_ROUNDS[code],
                    standings=standings,
                    competition_code=code,
                )
                M_a, elim_a, fight_a = calculate_motivation(
                    position=away_entry.position,
                    points=away_entry.points,
                    played_rounds=played_rounds,
                    total_rounds=TOTAL_ROUNDS[code],
                    standings=standings,
                    competition_code=code,
                )

                # Form
                F_h = calculate_form(season_matches=home_season_matches, coach_start_date=home_coach_start, is_home=True)
                F_a = calculate_form(season_matches=away_season_matches, coach_start_date=away_coach_start, is_home=False)

                # Coach strength
                c_home_raw = calculate_coach_strength(home_coach_matches, opponent_team_id=away_id)
                c_away_raw = calculate_coach_strength(away_coach_matches, opponent_team_id=home_id)
                C_home_norm, C_away_norm = normalize_coach_pair(c_home_raw, c_away_raw)

                # Ratings
                R_home = compute_rating(M_h, F_h, C_home_norm, elim_h, fight_a, wM, wF, wC)
                R_away = compute_rating(M_a, F_a, C_away_norm, elim_a, fight_h, wM, wF, wC)

                probs = compute_market_probabilities(R_home, R_away, avg_gf_home, avg_gf_away, h2h_stats)

                # Odds via API-Football
                league_id = LEAGUE_IDS_API_FOOTBALL.get(code)
                odds = None
                if league_id is not None:
                    fixture_id = af_client.find_fixture_id(
                        home_name=match.home_team.name,
                        away_name=match.away_team.name,
                        date_str=date_str,
                        league_id=league_id,
                        season=CURRENT_SEASON,
                    )
                    if fixture_id is not None:
                        odds = af_client.get_odds(fixture_id)

                markets = select_best_markets(probs, odds)
                best_market = markets[0]

                home_analysis = TeamAnalysis(
                    team=match.home_team,
                    motivation=M_h,
                    form=F_h,
                    coach_strength=C_home_norm,
                    rating=R_home,
                    eliminated=elim_h,
                    is_fighting=fight_h,
                )
                away_analysis = TeamAnalysis(
                    team=match.away_team,
                    motivation=M_a,
                    form=F_a,
                    coach_strength=C_away_norm,
                    rating=R_away,
                    eliminated=elim_a,
                    is_fighting=fight_a,
                )

                results.append(
                    MatchAnalysisResult(
                        match=match,
                        home_analysis=home_analysis,
                        away_analysis=away_analysis,
                        h2h=h2h_stats,
                        markets=markets,
                        best_market=best_market,
                        season_progress=g,
                    )
                )
            except Exception as e:
                logger.warning("Failed to analyze match %s (%s vs %s): %s", match.id, match.home_team.name, match.away_team.name, e)
                continue

    results.sort(key=lambda r: r.best_market.probability, reverse=True)
    return results


def analyze_single_match(
    home_team_name: str,
    away_team_name: str,
    date_str: str,
    fd_client: FootballDataClient,
    af_client: ApiFootballClient,
) -> Optional[MatchAnalysisResult]:
    """Найти матч в результатах analyze_matches_for_date и вернуть его анализ."""
    results = analyze_matches_for_date(date_str, fd_client, af_client)

    def normalize(s: str) -> str:
        import re

        return re.sub(r"[^a-z0-9]", "", s.lower())

    h_norm = normalize(home_team_name)
    a_norm = normalize(away_team_name)

    from difflib import SequenceMatcher

    best, best_score = None, 0.0
    for r in results:
        h = normalize(r.match.home_team.name)
        a = normalize(r.match.away_team.name)
        score = (SequenceMatcher(None, h_norm, h).ratio() + SequenceMatcher(None, a_norm, a).ratio()) / 2
        if score > best_score:
            best_score, best = score, r

    return best if best_score >= 0.6 else None

