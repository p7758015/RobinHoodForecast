"""Mock-based tests for Telegram match analysis application service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.services.telegram_match_analysis_service import TelegramMatchAnalysisService
from football_agent.tests.test_scorer_v2 import make_snapshot


def _scored_run_stub() -> ScoredRunV2:
    snap = make_snapshot(with_odds=True)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = ["odds"]
    report.merge_warnings = []
    report.odds_link_strategy = "none"
    return ScoredRunV2(snapshot=snap, prediction=pred, build_report=report)


def test_url_path_success() -> None:
    pipeline = MagicMock()
    pipeline.analyze_flashscore_url.return_value = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(),
        run_id="run-abc",
        match_key="mk-1",
        persisted=True,
        sources={"flashscore": "ok"},
    )
    service = TelegramMatchAnalysisService(pipeline=pipeline)

    resp = service.analyze_text(
        "https://www.flashscore.com/match/football/a/b/?mid=dC2J6FlK",
    )

    assert resp.success is True
    assert resp.analysis_path == "flashscore_url"
    assert resp.persisted is True
    assert resp.run_id == "run-abc"
    assert "Home — Away" in resp.reply_text
    assert "Лучший рынок" in resp.reply_text
    pipeline.analyze_flashscore_url.assert_called_once()


def test_unsupported_input_fallback() -> None:
    service = TelegramMatchAnalysisService(pipeline=MagicMock())
    resp = service.analyze_text("random text without teams")
    assert resp.success is False
    assert resp.stage_failed == "unsupported_input"
    assert "Flashscore" in resp.reply_text


def test_team_query_failure_graceful() -> None:
    pipeline = MagicMock()
    pipeline.analyze_teams.return_value = LivePipelineResult(
        success=False,
        path="team_query",
        stage_failed="flashscore_ingest",
        user_message="Не удалось найти матч",
        sources={"flashscore": "failed"},
    )
    service = TelegramMatchAnalysisService(pipeline=pipeline)

    resp = service.analyze_text("Team A — Team B")

    assert resp.success is False
    assert resp.analysis_path == "team_query"
    assert "Не удалось найти матч" in resp.reply_text


def test_enriched_analysis_with_odds_and_openclaw() -> None:
    pipeline = MagicMock()
    pipeline.analyze_flashscore_url.return_value = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(),
        persisted=True,
        sources={"flashscore": "ok", "openclaw": "ok", "odds": "ok"},
        context_highlights=["Новости: Derby-like atmosphere expected"],
        openclaw_link_strategy="by_teams_and_date",
        odds_link_strategy="by_match_id",
    )
    service = TelegramMatchAnalysisService(pipeline=pipeline)
    resp = service.analyze_text(
        "https://www.flashscore.com/match/football/x/y/?mid=abc12345",
    )
    assert resp.success is True
    assert resp.sources.get("odds") == "ok"
    assert "линия" in resp.reply_text


def test_enriched_analysis_with_openclaw_sources() -> None:
    pipeline = MagicMock()
    pipeline.analyze_flashscore_url.return_value = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(),
        persisted=True,
        sources={"flashscore": "ok", "openclaw": "ok"},
        context_highlights=["Новости: Derby-like atmosphere expected"],
        openclaw_link_strategy="by_teams_and_date",
    )
    service = TelegramMatchAnalysisService(pipeline=pipeline)
    resp = service.analyze_text(
        "https://www.flashscore.com/match/football/x/y/?mid=abc12345",
    )
    assert resp.success is True
    assert resp.sources.get("openclaw") == "ok"
    assert "OpenClaw" in resp.reply_text
    assert "Новости" in resp.reply_text


def test_formatter_includes_warning_for_incomplete_data() -> None:
    pipeline = MagicMock()
    pipeline.analyze_flashscore_url.return_value = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(),
        persisted=False,
        sources={"flashscore": "ok"},
        warnings=["incomplete_data: odds"],
    )
    service = TelegramMatchAnalysisService(pipeline=pipeline)
    resp = service.analyze_text(
        "https://www.flashscore.com/match/football/x/y/?mid=abc12345",
    )
    assert "⚠️" in resp.reply_text or "неполные" in resp.reply_text.lower()
