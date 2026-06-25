"""Team scope assignment tests."""

from __future__ import annotations

from football_agent.news_context.team_scope import (
    build_team_scope,
    classify_ownership,
    extract_coach_name_scoped,
    split_signals_by_side,
)


def test_america_mg_aliases() -> None:
    scope = build_team_scope("America MG", "Criciuma", competition_country="Brazil")
    assert "coelho" in scope.home_aliases
    assert "criciuma" in scope.away_aliases
    assert "tigre" in scope.away_aliases


def test_lanterna_scoped_to_home() -> None:
    scope = build_team_scope("America MG", "Criciuma", competition_country="Brazil")
    text = "lanterna América-MG recebe o embalado Criciúma na Arena Independência"
    own = classify_ownership(text, scope)
    assert own.side in ("home", "both")
    assert own.confidence >= 0.35


def test_g6_scoped_to_away() -> None:
    scope = build_team_scope("America MG", "Criciuma", competition_country="Brazil")
    text = "Criciúma mira G-6 em duelo de opostos com o América-MG"
    own = classify_ownership(text, scope)
    assert own.side in ("away", "both")


def test_coach_umberto_home_not_away() -> None:
    scope = build_team_scope("America MG", "Criciuma", competition_country="Brazil")
    text = (
        "Para a partida, o técnico Umberto Louzer terá um desfalque: "
        "o goleiro Gustavo, herói do time mineiro"
    )
    home_name, home_conf = extract_coach_name_scoped(text, scope, side="home")
    away_name, _ = extract_coach_name_scoped(text, scope, side="away")
    assert home_name == "Umberto Louzer"
    assert home_conf >= 0.35
    assert away_name is None


def test_gustavo_injury_scoped_via_home_coach() -> None:
    scope = build_team_scope("America MG", "Criciuma", competition_country="Brazil")
    text = (
        "Para a partida, o técnico Umberto Louzer terá um desfalque: "
        "o goleiro Gustavo, herói do time mineiro"
    )
    own = classify_ownership(text, scope, home_coach="Umberto Louzer")
    assert own.side == "home"
    home, away, unassigned = split_signals_by_side([text], scope, home_coach="Umberto Louzer")
    assert len(home) == 1
    assert not away
    assert not unassigned
