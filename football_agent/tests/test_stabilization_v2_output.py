"""Stabilization: team resolver, v2 output, express routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.domain.models import Match, Team
from football_agent.llm.agent import _apply_parse_overrides, _heuristic_parse, parse_user_request
from football_agent.normalizers.team_name_resolver import resolve_match_by_teams, score_team_query
from football_agent.output.v2_user_output import (
    build_match_payload_from_result,
    format_v2_all_matches_text,
    format_v2_single_match_text,
)
from football_agent.tests.test_scorer_v2 import make_snapshot

UTC_IMPORT = __import__("datetime").timezone.utc


def _match(home_name: str, away_name: str, mid: int = 1) -> Match:
    from datetime import datetime

    return Match(
        id=mid,
        competition_code="PL",
        home_team=Team(id=1, name=home_name, short_name=home_name.split()[0]),
        away_team=Team(id=2, name=away_name, short_name=away_name.split()[0]),
        utc_date=datetime(2026, 5, 24, 15, 0, tzinfo=UTC_IMPORT),
        status="SCHEDULED",
        matchday=38,
    )


def test_tottenham_partial_resolves() -> None:
    m = _match("Tottenham Hotspur FC", "Everton FC")
    found, err = resolve_match_by_teams("Tottenham", "Everton", [m])
    assert err is None
    assert found is not None
    assert "Tottenham" in found.home_team.name


def test_russian_tottenham_resolves() -> None:
    m = _match("Tottenham Hotspur FC", "Everton FC")
    found, err = resolve_match_by_teams("Тоттенхэм", "Эвертон", [m])
    assert err is None
    assert found is not None


def test_manchester_city_russian() -> None:
    m = _match("Manchester City FC", "Arsenal FC")
    found, err = resolve_match_by_teams("Манчестер Сити", "Арсенал", [m])
    assert err is None
    assert "Manchester City" in found.home_team.name


def test_score_team_query_partial() -> None:
    team = Team(id=1, name="Tottenham Hotspur FC", short_name="Tottenham")
    assert score_team_query("Tottenham", team) >= 0.88


def test_single_match_output_has_market_line() -> None:
    result = __import__(
        "football_agent.scorers.league_scorer_v2", fromlist=["LeagueScorerV2"]
    ).LeagueScorerV2().score_snapshot(make_snapshot(with_odds=True))
    payload = build_match_payload_from_result(result)
    text = format_v2_single_match_text(payload)
    assert "Лучший рынок:" in text
    assert "1X" in text or "П1" in text or "кф" in text
    assert len(payload.get("top_picks") or []) >= 1


def test_all_matches_text_no_express_build() -> None:
    payload = {
        "date": "2026-05-24",
        "include_express": False,
        "matches": [
            {
                "match": {"competition": "PL", "home": "A", "away": "B"},
                "best_market": {
                    "market_key": "HOME_NOT_LOSE",
                    "probability": 0.74,
                    "book_odds": 1.42,
                    "label": "1X",
                },
                "top_markets": [],
            }
        ],
    }
    text = format_v2_all_matches_text(payload)
    assert "HOME_NOT_LOSE" in text or "1X" in text
    assert "кф 1.42" in text
    assert "Экспресс не собран" in text


def test_heuristic_all_matches_not_express() -> None:
    p = _heuristic_parse("прогноз на все матчи 2026-05-24", "2026-05-24")
    assert p["type"] == "all_matches"
    p2 = _apply_parse_overrides("экспресс кф 3.0 на 2026-05-24", {"type": "all_matches"})
    assert p2["type"] == "express"


@patch("football_agent.llm.agent._get_openai_client", return_value=None)
def test_parse_all_matches_without_openai(mock_client: MagicMock) -> None:
    p = parse_user_request("все матчи 2026-05-24")
    assert p["type"] == "all_matches"


@patch("football_agent.app_pipeline.USE_V2_PIPELINE", True)
@patch("football_agent.app_pipeline.LeagueAnalysisServiceV2")
@patch("football_agent.app_pipeline.ExpressBuilderV2")
def test_v2_all_matches_path_no_express_builder(mock_express: MagicMock, mock_svc: MagicMock) -> None:
    from football_agent.app_pipeline import handle_request

    inst = mock_svc.return_value
    snap = make_snapshot()
    pred = __import__(
        "football_agent.scorers.league_scorer_v2", fromlist=["LeagueScorerV2"]
    ).LeagueScorerV2().score_snapshot(snap)
    inst.analyze_matches_for_date.return_value = [pred]

    out = handle_request(
        {"type": "all_matches", "date": "2026-05-24"},
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    mock_express.assert_not_called()
    assert "Экспресс не собран" in out or "1X" in out or "кф" in out
