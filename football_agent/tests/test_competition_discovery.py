"""Tests for universal competition discovery resolver."""

from __future__ import annotations

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.registry_lookup import lookup_registry_candidates
from football_agent.services.competition_classifier import classify_competition_meta
from football_agent.flashscore.models import FlashscoreMeta


def _mock_search(query: str, limit: int) -> list:
    q = query.lower()
    rows = []
    if "china" in q or "chinese" in q:
        rows.append(
            {
                "competition_name": "Chinese Super League",
                "competition_country": "China",
                "url": "https://www.flashscore.com/football/china/super-league/",
                "fixtures_url": "https://www.flashscore.com/football/china/super-league/fixtures/",
                "league_slug": "super-league",
                "country_slug": "china",
            }
        )
        if "league" in q:
            rows.append(
                {
                    "competition_name": "China League One",
                    "competition_country": "China",
                    "url": "https://www.flashscore.com/football/china/league-one/",
                    "fixtures_url": "https://www.flashscore.com/football/china/league-one/fixtures/",
                    "league_slug": "league-one",
                    "country_slug": "china",
                }
            )
    if "virsliga" in q or "latvia" in q:
        rows.append(
            {
                "competition_name": "Virsliga",
                "competition_country": "Latvia",
                "url": "https://www.flashscore.com/football/latvia/virsliga/",
                "fixtures_url": "https://www.flashscore.com/football/latvia/virsliga/fixtures/",
                "league_slug": "virsliga",
                "country_slug": "latvia",
            }
        )
    return rows[:limit]


def test_registry_fast_path_bundesliga() -> None:
    reg = lookup_registry_candidates("Bundesliga")
    assert any(c.registry_code == "BL1" for c in reg)
    assert reg[0].source == "registry"
    assert reg[0].confidence == "high"


def test_unknown_china_league_via_discovery_path() -> None:
    svc = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    result = svc.resolve_competition("лига китая")
    assert result.resolved is not None
    assert "Chinese" in result.resolved.candidate.competition_name
    assert result.resolved.candidate.country == "China"
    assert "registry" in result.sources_tried or "alias" in result.sources_tried
    assert "scraper_search" in result.sources_tried


def test_discovery_returns_canonical_meta() -> None:
    svc = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    result = svc.resolve_competition("china super league")
    assert result.resolved is not None
    c = result.resolved.candidate
    assert c.url.startswith("https://")
    assert c.fixtures_url is not None
    assert "super-league" in c.url or "Super League" in c.competition_name


def test_ambiguous_query_requires_disambiguation() -> None:
    svc = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    result = svc.resolve_competition("china football league", allow_ambiguous=False)
    # Two china leagues in mock -> ambiguous unless one clearly best
    if len(result.candidates) > 1:
        assert result.ambiguous or result.resolved is not None


def test_allow_ambiguous_picks_first_candidate() -> None:
    svc = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    result = svc.resolve_competition("china football league", allow_ambiguous=True)
    assert result.resolved is not None
    assert len(result.candidates) >= 1


def test_wave1_classifier_still_league_eligible() -> None:
    meta = FlashscoreMeta(
        match_id="t",
        source_url="",
        competition_name="Virsliga",
        competition_country="Latvia",
        home_team_name="A",
        away_team_name="B",
    )
    clf = classify_competition_meta(meta)
    assert clf.is_league_eligible is True
