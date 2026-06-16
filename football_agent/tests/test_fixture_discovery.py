"""Tests for fixture discovery service (mocked scraper)."""

from __future__ import annotations

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.discovery.models import CompetitionCandidate, ResolvedCompetition
from football_agent.eval_pool.scope import resolve_pool_entry
from football_agent.tests.test_competition_discovery import _mock_search


def _mock_fixtures(url: str, date_from: str, date_to: str) -> list:
    return [
        {
            "match_id": "m1",
            "home_team_name": "Home FC",
            "away_team_name": "Away FC",
            "kickoff_utc": f"{date_from}T15:00:00+00:00",
            "date": date_from,
            "source_url": "https://flashscore.com/match/m1",
            "competition_name": "Generic League",
            "competition_country": "World",
            "status": "scheduled",
        }
    ]


def test_fixture_discovery_without_hardcoded_league_code() -> None:
    resolver = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    resolve = resolver.resolve_competition("latvia virsliga")
    assert resolve.resolved is not None

    svc = FixtureDiscoveryService(
        resolver=resolver,
        fixtures_fn=_mock_fixtures,
    )
    out = svc.list_competition_fixtures(resolve.resolved, date_from="2026-06-10")
    assert out.count == 1
    assert out.fixtures[0].home_team == "Home FC"
    assert out.fixtures[0].match_url


def test_no_fixtures_in_range_fail_soft() -> None:
    resolver = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    resolved = ResolvedCompetition(
        candidate=CompetitionCandidate(
            competition_name="Virsliga",
            country="Latvia",
            url="https://www.flashscore.com/football/latvia/virsliga/",
            fixtures_url="https://www.flashscore.com/football/latvia/virsliga/fixtures/",
            source="scraper_search",
            confidence="high",
        )
    )
    svc = FixtureDiscoveryService(resolver=resolver, fixtures_fn=lambda *_a: [])
    out = svc.list_competition_fixtures(resolved, date_from="2026-06-10")
    assert out.count == 0
    assert "no_fixtures_in_date_range" in out.warnings


def test_resolve_and_list_end_to_end_mock() -> None:
    svc = FixtureDiscoveryService(
        fixtures_fn=_mock_fixtures,
        resolver=CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False),
    )
    out = svc.resolve_and_list_fixtures("china super league", date_from="2026-06-10")
    assert out.count == 1
    assert out.competition.candidate.competition_name


def test_wave1_scope_unchanged() -> None:
    entry = resolve_pool_entry("Premier League", "Kazakhstan")
    assert entry is not None
    assert entry.key == "kazakhstan_premier"
