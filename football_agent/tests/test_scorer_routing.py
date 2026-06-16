"""Unit tests for scorer tournament routing decisions."""

from __future__ import annotations

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.scorers.routing import resolve_scorer_route
from football_agent.services.competition_classifier import CompetitionClassification


def _clf(
    category: CompetitionContextClass,
    *,
    tournament_type: TournamentType | None = None,
    confidence: str = "high",
    signals: list[str] | None = None,
) -> CompetitionClassification:
    tt = tournament_type or {
        CompetitionContextClass.LEAGUE: TournamentType.LEAGUE_REGULAR,
        CompetitionContextClass.DOMESTIC_CUP: TournamentType.DOMESTIC_CUP,
        CompetitionContextClass.INTERNATIONAL_CLUB: TournamentType.INTERNATIONAL_CLUB,
        CompetitionContextClass.NATIONAL_TEAM: TournamentType.INTERNATIONAL_NATIONAL,
        CompetitionContextClass.FRIENDLY: TournamentType.FRIENDLY,
        CompetitionContextClass.UNKNOWN: TournamentType.UNKNOWN,
    }[category]
    return CompetitionClassification(
        category=category,
        tournament_type=tt,
        confidence=confidence,
        signals=signals or [],
    )


def test_resolve_league_full() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.LEAGUE))
    assert decision.route == "league_full"
    assert decision.league_eligible is True
    assert decision.reason == "league_full:high_confidence_league"


def test_resolve_domestic_cup_parked() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.DOMESTIC_CUP))
    assert decision.route == "non_league_parked"
    assert decision.reason == "parked:domestic_cup"


def test_resolve_international_club_parked() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.INTERNATIONAL_CLUB))
    assert decision.route == "non_league_parked"
    assert decision.reason == "parked:international_club"


def test_resolve_national_team_parked() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.NATIONAL_TEAM))
    assert decision.route == "non_league_parked"
    assert decision.reason == "parked:national_team"


def test_resolve_friendly_parked() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.FRIENDLY))
    assert decision.route == "non_league_parked"
    assert decision.reason == "parked:friendly"


def test_resolve_unknown_parked() -> None:
    decision = resolve_scorer_route(_clf(CompetitionContextClass.UNKNOWN))
    assert decision.route == "unknown_parked"
    assert decision.reason == "parked:unknown"


def test_resolve_none_classification_without_snapshot() -> None:
    decision = resolve_scorer_route(None)
    assert decision.route == "unknown_parked"
    assert decision.reason == "parked:unknown"


def test_league_low_confidence_not_eligible() -> None:
    decision = resolve_scorer_route(
        _clf(CompetitionContextClass.LEAGUE, confidence="low"),
    )
    assert decision.route == "non_league_parked"
    assert decision.league_eligible is False
