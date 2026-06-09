"""
League v2 orchestration: scheduled matches → snapshot → scorer → predictions.

Example::

    from football_agent.data_providers.football_data_client import FootballDataClient
    from football_agent.data_providers.api_football_client import ApiFootballClient
    from football_agent.services.league_analysis_service_v2 import LeagueAnalysisServiceV2

    fd = FootballDataClient()
    af = ApiFootballClient()
    service = LeagueAnalysisServiceV2(fd, af)
    results = service.analyze_matches_for_date("2024-04-25", competition_code="PL")
    for r in results:
        print(r.match_meta.home_team.name, "vs", r.match_meta.away_team.name)
        print(r.best_market.market_key, r.best_market.probability)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.models import Match
from football_agent.domain.models_v2 import MatchPredictionResultV2
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder
from football_agent.normalizers.team_name_resolver import resolve_match_by_teams
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2

logger = logging.getLogger(__name__)


class LeagueAnalysisServiceV2:
    """Thin league pipeline: FootballData scheduled matches → v2 predictions."""

    def __init__(
        self,
        football_data_client: FootballDataClient,
        api_football_client: ApiFootballClient,
        snapshot_builder: Optional[MatchSnapshotBuilder] = None,
        scorer: Optional[LeagueScorerV2] = None,
    ) -> None:
        self._fd = football_data_client
        self._af = api_football_client
        self._builder = snapshot_builder or MatchSnapshotBuilder(football_data_client, api_football_client)
        self._scorer = scorer or LeagueScorerV2()

    def analyze_matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[MatchPredictionResultV2]:
        matches = self._matches_for_date(date_str, competition_code)
        total = len(matches)
        logger.info(
            "League v2 analysis: date=%s competition=%s matches_found=%d",
            date_str,
            competition_code or "ALL",
            total,
        )

        results: List[MatchPredictionResultV2] = []
        skipped = 0
        for match in matches:
            try:
                results.append(self.analyze_single_match(match))
            except Exception as e:
                skipped += 1
                logger.exception(
                    "Skipped match id=%s %s vs %s: %s",
                    match.id,
                    match.home_team.name,
                    match.away_team.name,
                    e,
                )

        results = self._sort_results(results)
        logger.info(
            "League v2 analysis done: date=%s competition=%s ok=%d skipped=%d",
            date_str,
            competition_code or "ALL",
            len(results),
            skipped,
        )
        return results

    def analyze_single_match(self, match: Match) -> MatchPredictionResultV2:
        snapshot = self._builder.build_snapshot_for_match(match)
        return self._scorer.score_snapshot(snapshot)

    def find_match_by_teams(
        self,
        home_team_name: str,
        away_team_name: str,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> Tuple[Optional[Match], Optional[str]]:
        matches = self._matches_for_date(date_str, competition_code)
        return resolve_match_by_teams(home_team_name, away_team_name, matches)

    def analyze_match_by_teams(
        self,
        home_team_name: str,
        away_team_name: str,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> Tuple[Optional[MatchPredictionResultV2], Optional[str]]:
        match, err = self.find_match_by_teams(
            home_team_name, away_team_name, date_str, competition_code
        )
        if err:
            logger.warning("Match resolve: %s", err)
            return None, err
        if not match:
            return None, "Матч не найден."
        return self.analyze_single_match(match), None

    def _matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str],
    ) -> List[Match]:
        matches = self._fd.get_matches_by_date(date_str)
        if competition_code:
            code = competition_code.upper()
            matches = [m for m in matches if m.competition_code.upper() == code]
        return sorted(
            matches,
            key=lambda m: (m.utc_date, m.id, m.home_team.name, m.away_team.name),
        )

    @staticmethod
    def _sort_results(results: List[MatchPredictionResultV2]) -> List[MatchPredictionResultV2]:
        return sorted(
            results,
            key=lambda r: (
                r.match_meta.match_date_utc,
                r.match_meta.match_id,
                r.match_meta.home_team.name,
                r.match_meta.away_team.name,
            ),
        )
