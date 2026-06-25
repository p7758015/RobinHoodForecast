"""Tests for FlashscoreDiscoveryClient results endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient


def test_fetch_competition_results_parses_matches() -> None:
    client = FlashscoreDiscoveryClient("http://localhost:3000")
    payload = {
        "matches": [
            {
                "match_id": "ELlQ3VfC",
                "home_team_name": "Kaisar Kyzylorda",
                "away_team_name": "Ulytau",
                "status": "finished",
                "home_score": 0,
                "away_score": 0,
                "kickoff_utc": "2026-06-20T18:00:00+00:00",
            }
        ]
    }
    with patch("football_agent.discovery.scraper_client.get_json", return_value=payload):
        rows = client.fetch_competition_results(
            "https://www.flashscore.com/football/kazakhstan/premier-league/",
            date_from="2026-06-18",
            date_to="2026-06-21",
        )
    assert len(rows) == 1
    assert rows[0]["match_id"] == "ELlQ3VfC"
    assert rows[0]["status"] == "finished"


def test_fetch_competition_results_enrich_detail_param() -> None:
    client = FlashscoreDiscoveryClient("http://localhost:3000")
    mock_get = MagicMock(return_value={"matches": []})
    with patch("football_agent.discovery.scraper_client.get_json", mock_get):
        client.fetch_competition_results(
            "https://www.flashscore.com/football/kazakhstan/premier-league/",
            date_from="2026-06-18",
            date_to="2026-06-21",
            enrich_detail=True,
        )
    _session, url, kwargs = mock_get.call_args[0][0], mock_get.call_args[0][1], mock_get.call_args[1]
    assert url.endswith("/v1/competitions/results")
    assert kwargs["params"]["enrich_detail"] == "true"
