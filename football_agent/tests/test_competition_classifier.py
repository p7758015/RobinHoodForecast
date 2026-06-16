"""Competition context classification tests."""

from __future__ import annotations

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.models import FlashscoreMeta, FlashscoreMatchFacts, FlashscoreProvenance
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


def test_league_classification() -> None:
    clf = classify_competition_meta(_meta(competition_name="Premier League"))
    assert clf.category == CompetitionContextClass.LEAGUE
    assert clf.confidence == "high"


def test_domestic_cup_classification() -> None:
    clf = classify_competition_meta(_meta(competition_name="FA Cup"))
    assert clf.category == CompetitionContextClass.DOMESTIC_CUP
    assert clf.tournament_type == TournamentType.DOMESTIC_CUP


def test_international_club_classification() -> None:
    clf = classify_competition_meta(_meta(competition_name="UEFA Champions League"))
    assert clf.category == CompetitionContextClass.INTERNATIONAL_CLUB


def test_national_team_competition_name() -> None:
    clf = classify_competition_meta(_meta(competition_name="World Cup Qualification UEFA"))
    assert clf.category == CompetitionContextClass.NATIONAL_TEAM


def test_national_team_from_team_names_low_confidence() -> None:
    clf = classify_competition_meta(
        _meta(competition_name="International", home_team_name="Brazil", away_team_name="Argentina"),
    )
    assert clf.category == CompetitionContextClass.NATIONAL_TEAM
    assert clf.confidence == "low"


def test_friendly_classification() -> None:
    clf = classify_competition_meta(_meta(competition_name="Club Friendlies"))
    assert clf.category == CompetitionContextClass.FRIENDLY


def test_unknown_ambiguous() -> None:
    clf = classify_competition_meta(_meta(competition_name="Regional Tournament XYZ"))
    assert clf.category == CompetitionContextClass.UNKNOWN
    assert clf.tournament_type == TournamentType.UNKNOWN
    assert clf.confidence == "low"
    assert clf.is_league_eligible is False


def test_explicit_tournament_type_trusted() -> None:
    clf = classify_competition_meta(
        _meta(competition_name="Anything", tournament_type=TournamentType.DOMESTIC_CUP),
    )
    assert clf.category == CompetitionContextClass.DOMESTIC_CUP
    assert clf.source == "explicit_tournament_type"


def test_refine_meta_sets_tournament_type_for_cup() -> None:
    meta = refine_meta_tournament_type(_meta(competition_name="Coppa Italia"))
    assert meta.tournament_type == TournamentType.DOMESTIC_CUP


def test_botola_fixture_stays_league() -> None:
    from pathlib import Path

    from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
    from football_agent.flashscore.service import FlashscoreIngestionService

    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(Path(__file__).parent / "data"),
    ).get_facts_for_match("flashscore_botola_sample_match")
    assert facts is not None
    clf = classify_competition_meta(facts.meta)
    assert clf.category == CompetitionContextClass.LEAGUE
