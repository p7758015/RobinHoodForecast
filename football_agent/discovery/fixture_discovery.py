"""Fixture list discovery for a resolved competition."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from football_agent import config
from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.models import (
    CompetitionCandidate,
    DiscoveredFixture,
    FixtureDiscoveryResult,
    ResolvedCompetition,
)
from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient

logger = logging.getLogger(__name__)


def _fixture_from_raw(raw: dict) -> DiscoveredFixture:
    match_id = str(raw.get("match_id") or raw.get("id") or "")
    url = str(raw.get("source_url") or raw.get("url") or "").strip()
    home = str(raw.get("home_team_name") or raw.get("home_team") or raw.get("home") or "")
    away = str(raw.get("away_team_name") or raw.get("away_team") or raw.get("away") or "")
    kickoff = raw.get("kickoff_utc") or raw.get("date")
    match_date = None
    if kickoff:
        text = str(kickoff)
        if len(text) >= 10:
            match_date = text[:10]
    return DiscoveredFixture(
        match_id=match_id or f"fs-{home}-{away}",
        match_url=url,
        home_team=home,
        away_team=away,
        kickoff_utc=str(kickoff) if kickoff else None,
        match_date=match_date,
        status=str(raw.get("status") or "scheduled"),
        competition_name=raw.get("competition_name"),
        competition_country=raw.get("competition_country"),
        raw=raw,
    )


class FixtureDiscoveryService:
    """
    list_competition_fixtures(resolved_competition, date_from, date_to) -> fixtures

    Uses scraper generic competition fixtures endpoint (competition_url based).
    """

    def __init__(
        self,
        *,
        scraper_url: Optional[str] = None,
        scraper_api_key: Optional[str] = None,
        discovery_client: Optional[FlashscoreDiscoveryClient] = None,
        fixtures_fn: Optional[Callable[[str, str, str], List[dict]]] = None,
        resolver: Optional[CompetitionResolverService] = None,
    ) -> None:
        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        self._client = discovery_client or (
            FlashscoreDiscoveryClient(base, api_key=scraper_api_key or config.FLASHSCORE_SCRAPER_API_KEY)
            if base
            else None
        )
        self._fixtures_fn = fixtures_fn
        self._resolver = resolver or CompetitionResolverService(
            scraper_url=base or None,
            discovery_client=self._client,
        )

    def resolve_and_list_fixtures(
        self,
        query_text: str,
        *,
        date_from: str,
        date_to: Optional[str] = None,
        allow_ambiguous: bool = False,
    ) -> FixtureDiscoveryResult:
        """Convenience: resolve competition from text, then list fixtures."""
        resolve = self._resolver.resolve_competition(query_text, allow_ambiguous=allow_ambiguous)
        if resolve.resolved is None:
            warnings = list(resolve.warnings)
            if resolve.ambiguous:
                warnings.append("competition_unresolved_ambiguous")
            else:
                warnings.append("competition_unresolved")
            placeholder_candidate = (
                resolve.candidates[0]
                if resolve.candidates
                else CompetitionCandidate(
                    competition_name=query_text,
                    country=None,
                    url="",
                    source="unresolved",
                    confidence="low",
                )
            )
            placeholder = ResolvedCompetition(candidate=placeholder_candidate, ambiguous=True)
            return FixtureDiscoveryResult(
                competition=placeholder,
                date_from=date_from,
                date_to=date_to or date_from,
                fixtures=[],
                warnings=warnings,
            )
        return self.list_competition_fixtures(
            resolve.resolved,
            date_from=date_from,
            date_to=date_to,
        )

    def list_competition_fixtures(
        self,
        competition: ResolvedCompetition,
        *,
        date_from: str,
        date_to: Optional[str] = None,
    ) -> FixtureDiscoveryResult:
        end = date_to or date_from
        url = competition.candidate.fixtures_url or competition.candidate.url
        warnings: List[str] = []

        if not url:
            return FixtureDiscoveryResult(
                competition=competition,
                date_from=date_from,
                date_to=end,
                fixtures=[],
                warnings=["missing_competition_url"],
            )

        raw_list = self._fetch_fixtures(url, date_from, end)
        if not raw_list:
            warnings.append("no_fixtures_in_date_range")

        fixtures = [_fixture_from_raw(r) for r in raw_list if r.get("home_team_name") or r.get("home")]
        return FixtureDiscoveryResult(
            competition=competition,
            date_from=date_from,
            date_to=end,
            fixtures=fixtures,
            warnings=warnings,
        )

    def _fetch_fixtures(self, competition_url: str, date_from: str, date_to: str) -> List[dict]:
        if self._fixtures_fn is not None:
            return self._fixtures_fn(competition_url, date_from, date_to)
        if self._client is None:
            logger.warning("Flashscore discovery client not configured")
            return []
        try:
            return self._client.fetch_competition_fixtures(
                competition_url,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:
            logger.warning("fixture discovery failed: %s", exc)
            return []
