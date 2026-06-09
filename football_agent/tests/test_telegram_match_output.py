"""Formatter tests for Telegram match replies."""

from __future__ import annotations

from unittest.mock import MagicMock

from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.tests.test_scorer_v2 import make_snapshot


def test_format_telegram_match_reply_structure() -> None:
    snap = make_snapshot(with_odds=True)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = []
    report.merge_warnings = []
    report.odds_link_strategy = "fixture"
    scored = ScoredRunV2(snapshot=snap, prediction=pred, build_report=report)

    text = format_telegram_match_reply(
        LivePipelineResult(success=True, path="flashscore_url", scored_run=scored),
    )

    assert "⚽" in text
    assert "Лучший рынок" in text
    assert "Уверенность" in text
    assert len(text.splitlines()) <= 15
