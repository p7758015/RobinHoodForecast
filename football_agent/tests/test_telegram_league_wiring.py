"""Tests for league query parsing and Telegram league analysis wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from football_agent.bot.request_parser import MatchRequestKind, parse_match_request
from football_agent.discovery.models import CompetitionCandidate, DiscoveredFixture, FixtureDiscoveryResult, ResolvedCompetition
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.telegram_league_analysis_service import TelegramLeagueAnalysisService
from football_agent.services.telegram_match_analysis_service import TelegramMatchAnalysisService
from football_agent.tests.test_competition_discovery import _mock_search


def test_parse_league_query_china() -> None:
    req = parse_match_request("дай прогноз на лигу Китая")
    assert req.kind == MatchRequestKind.LEAGUE_QUERY
    assert req.league_phrase
    assert "кит" in req.league_phrase.lower() or "Кит" in req.league_phrase


def test_parse_league_before_team_pair() -> None:
    req = parse_match_request("дай прогноз на серию B бразилии")
    assert req.kind == MatchRequestKind.LEAGUE_QUERY
    assert req.kind != MatchRequestKind.TEAM_QUERY


def test_registry_league_phrase_still_team_if_separator() -> None:
    req = parse_match_request("Real Madrid vs Barcelona")
    assert req.kind == MatchRequestKind.TEAM_QUERY


def _mock_resolver_and_fixtures():
    from football_agent.discovery.competition_resolver import CompetitionResolverService
    from football_agent.discovery.fixture_discovery import FixtureDiscoveryService

    resolver = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    fixture_svc = FixtureDiscoveryService(
        resolver=resolver,
        fixtures_fn=lambda url, d_from, d_to: [
            {
                "match_id": "m1",
                "home_team_name": "Home",
                "away_team_name": "Away",
                "source_url": "https://flashscore.com/match/m1",
                "date": d_from,
            }
        ],
    )
    return resolver, fixture_svc


def test_league_service_builds_pipeline_calls() -> None:
    resolver, fixture_svc = _mock_resolver_and_fixtures()
    calls: list[str] = []

    def _run(url: str) -> LivePipelineResult:
        calls.append(url)
        return LivePipelineResult(success=True, path="flashscore_url", persisted=True)

    svc = TelegramLeagueAnalysisService(
        resolver=resolver,
        fixture_svc=fixture_svc,
        pipeline_runner=_run,
        max_matches=3,
    )
    req = parse_match_request("дай прогноз на лигу Китая")
    assert req.kind == MatchRequestKind.LEAGUE_QUERY
    resp = svc.analyze_league_request(req)
    assert resp.success is True
    assert len(calls) == 1
    assert "Chinese" in resp.reply_text or "China" in resp.reply_text


def test_league_service_ambiguous_lists_candidates() -> None:
    from football_agent.discovery.competition_resolver import CompetitionResolverService
    from football_agent.discovery.models import CompetitionResolveResult

    resolver = MagicMock()
    resolver.resolve_competition.return_value = CompetitionResolveResult(
        query="china",
        candidates=[
            CompetitionCandidate("A", "China", "http://a", source="scraper_search"),
            CompetitionCandidate("B", "China", "http://b", source="scraper_search"),
        ],
        resolved=None,
        ambiguous=True,
        warnings=["ambiguous_competition_query"],
    )
    svc = TelegramLeagueAnalysisService(resolver=resolver)
    req = parse_match_request("дай прогноз на лигу китая")
    resp = svc.analyze_league_request(req)
    assert resp.success is False
    assert resp.needs_clarification is True
    assert "уточните" in resp.reply_text.lower() or "несколько" in resp.reply_text.lower()


def test_league_service_no_fixtures_fail_soft() -> None:
    from football_agent.discovery.competition_resolver import CompetitionResolverService
    from football_agent.discovery.fixture_discovery import FixtureDiscoveryService

    resolver = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    fixture_svc = FixtureDiscoveryService(
        resolver=resolver,
        fixtures_fn=lambda *_a: [],
    )
    svc = TelegramLeagueAnalysisService(resolver=resolver, fixture_svc=fixture_svc)
    req = parse_match_request("дай прогноз на лигу Китая")
    resp = svc.analyze_league_request(req)
    assert resp.success is False
    assert "матчей" in resp.reply_text.lower()


def test_telegram_match_service_routes_league_query() -> None:
    resolver, fixture_svc = _mock_resolver_and_fixtures()
    league_svc = TelegramLeagueAnalysisService(
        resolver=resolver,
        fixture_svc=fixture_svc,
        pipeline_runner=lambda _u: LivePipelineResult(success=True, path="flashscore_url"),
    )
    match_svc = TelegramMatchAnalysisService(league_service=league_svc)
    resp = match_svc.analyze_text("дай прогноз на лигу Китая")
    assert resp.request_kind == "league_query"
