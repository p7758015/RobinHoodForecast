"""Tests for Telegram request parsing (transport-agnostic)."""

from __future__ import annotations

from football_agent.bot.request_parser import MatchRequestKind, parse_match_request


def test_parse_flashscore_url_with_mid() -> None:
    text = (
        "https://www.flashscore.com/match/football/kawkab-marrakech-lA6RXjzH/"
        "raja-casablanca-vTnNkCKc/?mid=dC2J6FlK"
    )
    req = parse_match_request(text)
    assert req.kind == MatchRequestKind.FLASHSCORE_URL
    assert req.flashscore_url is not None
    assert "mid=dC2J6FlK" in req.flashscore_url


def test_parse_team_dash_query() -> None:
    req = parse_match_request("FAR Rabat — Maghreb Fez")
    assert req.kind == MatchRequestKind.TEAM_QUERY
    assert req.home_team == "FAR Rabat"
    assert req.away_team == "Maghreb Fez"


def test_parse_team_vs_with_date() -> None:
    req = parse_match_request("Brazil vs Argentina 2026-06-13")
    assert req.kind == MatchRequestKind.TEAM_QUERY
    assert req.home_team == "Brazil"
    assert req.away_team == "Argentina"
    assert req.date_str == "2026-06-13"


def test_unsupported_gibberish() -> None:
    req = parse_match_request("привет как дела")
    assert req.kind == MatchRequestKind.UNSUPPORTED
