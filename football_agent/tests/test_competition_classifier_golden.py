"""Golden classifier coverage for production-first tournament types."""

from __future__ import annotations

import pytest

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.models import FlashscoreMeta
from football_agent.services.competition_classifier import (
    classify_competition_meta,
    refine_meta_tournament_type,
)


def _meta(**kwargs) -> FlashscoreMeta:
    defaults = {
        "match_id": "t1",
        "source_url": "https://example.com",
        "competition_name": "Test",
        "home_team_name": "Home FC",
        "away_team_name": "Away FC",
    }
    defaults.update(kwargs)
    return FlashscoreMeta(**defaults)


def _assert_clf(
    meta: FlashscoreMeta,
    *,
    category: CompetitionContextClass,
    tournament_type: TournamentType,
    confidence: str = "high",
) -> None:
    clf = classify_competition_meta(meta)
    assert clf.category == category, (meta.competition_name, clf)
    assert clf.tournament_type == tournament_type, (meta.competition_name, clf)
    assert clf.confidence == confidence, (meta.competition_name, clf)


@pytest.mark.parametrize(
    "competition_name,country",
    [
        ("Premier League", "England"),
        ("LaLiga", "Spain"),
        ("Primera Division", "Spain"),
        ("Serie A", "Italy"),
        ("Bundesliga", "Germany"),
        ("Ligue 1", "France"),
        ("Championship", "England"),
        ("Eredivisie", "Netherlands"),
        ("Liga MX", "Mexico"),
        ("MLS", "USA"),
        ("Major League Soccer", "USA"),
        ("Saudi Pro League", "Saudi Arabia"),
        ("J1 League", "Japan"),
        ("Botola Pro", "Morocco"),
        ("Brazil Serie B", "Brazil"),
        ("Serie B", "Italy"),
        ("Meistriliiga", "Estonia"),
        ("Premium Liiga", "Estonia"),
        ("Virsliga", "Latvia"),
        ("Premier League", "Kazakhstan"),
        ("Primeira Liga", "Portugal"),
        ("Scottish Premiership", "Scotland"),
        ("Super Lig", "Turkey"),
        ("Ekstraklasa", "Poland"),
        ("Allsvenskan", "Sweden"),
        ("Eliteserien", "Norway"),
    ],
)
def test_golden_league(competition_name: str, country: str) -> None:
    meta = _meta(competition_name=competition_name, competition_country=country)
    _assert_clf(meta, category=CompetitionContextClass.LEAGUE, tournament_type=TournamentType.LEAGUE_REGULAR)
    assert classify_competition_meta(meta).is_league_eligible is True


@pytest.mark.parametrize(
    "competition_name",
    [
        "FA Cup",
        "Copa del Rey",
        "DFB-Pokal",
        "Taca de Portugal",
        "KNVB Beker",
        "Supercoppa Italiana",
        "Supercopa de Espana",
        "EFL Cup",
        "Carabao Cup",
    ],
)
def test_golden_domestic_cup(competition_name: str) -> None:
    _assert_clf(
        _meta(competition_name=competition_name),
        category=CompetitionContextClass.DOMESTIC_CUP,
        tournament_type=TournamentType.DOMESTIC_CUP,
    )


@pytest.mark.parametrize(
    "competition_name",
    [
        "UEFA Champions League",
        "UEFA Europa League",
        "UEFA Conference League",
        "UEFA Champions League Qualifying",
        "Champions League Qualification",
        "Europa League Qualifiers",
        "Copa Libertadores",
        "AFC Champions League",
        "FIFA Club World Cup",
        "Leagues Cup",
        "UEFA Super Cup",
        "CONCACAF Champions Cup",
    ],
)
def test_golden_international_club(competition_name: str) -> None:
    _assert_clf(
        _meta(competition_name=competition_name),
        category=CompetitionContextClass.INTERNATIONAL_CLUB,
        tournament_type=TournamentType.INTERNATIONAL_CLUB,
    )


@pytest.mark.parametrize(
    "competition_name",
    [
        "FIFA World Cup",
        "FIFA World Cup Qualifiers",
        "World Cup Qualification UEFA",
        "UEFA European Championship",
        "UEFA EURO 2024",
        "UEFA Euro Qualifiers",
        "UEFA Nations League",
        "Copa America",
        "Africa Cup of Nations",
        "AFCON",
        "Asian Cup",
        "Gold Cup",
        "CONCACAF Nations League",
    ],
)
def test_golden_national_team(competition_name: str) -> None:
    _assert_clf(
        _meta(competition_name=competition_name),
        category=CompetitionContextClass.NATIONAL_TEAM,
        tournament_type=TournamentType.INTERNATIONAL_NATIONAL,
    )


@pytest.mark.parametrize(
    "competition_name",
    [
        "Club Friendlies",
        "International Friendlies",
        "Audi Cup",
        "Florida Cup",
    ],
)
def test_golden_friendly(competition_name: str) -> None:
    _assert_clf(
        _meta(competition_name=competition_name),
        category=CompetitionContextClass.FRIENDLY,
        tournament_type=TournamentType.FRIENDLY,
    )


def test_ucl_qualifying_not_national_team() -> None:
    clf = classify_competition_meta(_meta(competition_name="UEFA Champions League Qualifying"))
    assert clf.category == CompetitionContextClass.INTERNATIONAL_CLUB
    assert clf.category != CompetitionContextClass.NATIONAL_TEAM


def test_unknown_not_league_eligible() -> None:
    clf = classify_competition_meta(_meta(competition_name="Regional Tournament XYZ"))
    assert clf.category == CompetitionContextClass.UNKNOWN
    assert clf.tournament_type == TournamentType.UNKNOWN
    assert clf.is_league_eligible is False


def test_refine_unknown_sets_tournament_type_unknown() -> None:
    meta = refine_meta_tournament_type(_meta(competition_name="Regional Tournament XYZ"))
    assert meta.tournament_type == TournamentType.UNKNOWN


def test_refine_league_sets_tournament_type() -> None:
    meta = refine_meta_tournament_type(_meta(competition_name="Premier League", competition_country="England"))
    assert meta.tournament_type == TournamentType.LEAGUE_REGULAR


def test_domestic_super_cup_not_uefa_super_cup() -> None:
    _assert_clf(
        _meta(competition_name="Supercopa de Espana"),
        category=CompetitionContextClass.DOMESTIC_CUP,
        tournament_type=TournamentType.DOMESTIC_CUP,
    )
    _assert_clf(
        _meta(competition_name="UEFA Super Cup"),
        category=CompetitionContextClass.INTERNATIONAL_CLUB,
        tournament_type=TournamentType.INTERNATIONAL_CLUB,
    )
