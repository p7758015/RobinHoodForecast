"""Tests for eval wave pool competition mapping coverage."""

from __future__ import annotations

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.registry_lookup import lookup_registry_by_pool_entry
from football_agent.eval_pool.fixture_sources import fetch_fixtures_for_pool_entry
from football_agent.eval_pool.scope import filter_pool_keys


def test_kazakhstan_pool_entry_resolves_with_url() -> None:
    entry = filter_pool_keys(["kazakhstan_premier"])[0]
    cand = lookup_registry_by_pool_entry(entry)
    assert cand is not None
    assert cand.url.endswith("/football/kazakhstan/premier-league/")
    resolve = CompetitionResolverService().resolve_competition_for_pool_entry(entry)
    assert resolve.resolved is not None
    assert resolve.ambiguous is False
    assert "premier-league" in resolve.resolved.candidate.url


def test_brazil_pool_entry_prefers_registry_url_over_alias() -> None:
    entry = filter_pool_keys(["brazil_serie_b"])[0]
    resolve = CompetitionResolverService().resolve_competition_for_pool_entry(entry)
    assert resolve.resolved is not None
    assert resolve.resolved.candidate.url.endswith("/football/brazil/serie-b/")


def test_estonia_premium_maps_to_women_league_url() -> None:
    entry = filter_pool_keys(["estonia_premium_liiga"])[0]
    cand = lookup_registry_by_pool_entry(entry)
    assert cand is not None
    assert "meistriliiga-women" in cand.url


def test_chile_and_lithuania_registry_urls() -> None:
    chile = lookup_registry_by_pool_entry(filter_pool_keys(["chile_primera"])[0])
    lith = lookup_registry_by_pool_entry(filter_pool_keys(["lithuania_a_lyga"])[0])
    assert chile is not None and "liga-de-primera" in chile.url
    assert lith is not None and "/a-lyga/" in lith.url


def test_fetch_fixtures_mock_discovery_in_range_with_display_time() -> None:
    entry = filter_pool_keys(["estonia_meistriliiga"])[0]

    class _FakeFixture:
        def __init__(self) -> None:
            self.match_id = "est-20"
            self.match_url = "https://flashscore.com/match/?mid=est-20"
            self.home_team = "Levadia"
            self.away_team = "Kalju"
            self.kickoff_utc = None
            self.match_date = None
            self.status = "scheduled"
            self.competition_name = "Meistriliiga"
            self.competition_country = "Estonia"
            self.raw = {"time": "20.06. 17:00"}

    class _FakeDiscovered:
        fixtures = [_FakeFixture()]
        warnings: list[str] = []

    class _FakeFixtureSvc:
        def list_competition_fixtures(self, *_a, **_kw):
            return _FakeDiscovered()

    class _FakeResolver:
        def resolve_competition_for_pool_entry(self, _entry):
            from football_agent.discovery.models import CompetitionCandidate, CompetitionResolveResult, ResolvedCompetition

            c = CompetitionCandidate(
                "Meistriliiga",
                "Estonia",
                "https://www.flashscore.com/football/estonia/meistriliiga/",
                fixtures_url="https://www.flashscore.com/football/estonia/meistriliiga/fixtures/",
                source="test",
            )
            return CompetitionResolveResult(
                query="x",
                candidates=[c],
                resolved=ResolvedCompetition(candidate=c),
                ambiguous=False,
                warnings=[],
            )

    result = fetch_fixtures_for_pool_entry(
        entry,
        "2026-06-20",
        [],
        use_discovery_fallback=True,
        wave_date_from="2026-06-18",
        wave_date_to="2026-06-21",
        resolver=_FakeResolver(),
        fixture_svc=_FakeFixtureSvc(),
    )
    assert result.stats.in_range == 1
    assert result.fixtures[0]["date"] == "2026-06-20"
