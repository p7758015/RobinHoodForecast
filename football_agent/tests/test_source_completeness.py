"""Source completeness and provenance reporting tests."""

from __future__ import annotations

from pathlib import Path

from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.services.http_fetch_result import classify_http_error_message
from football_agent.services.source_completeness import (
    build_completeness_report,
    format_telegram_completeness_hint,
)

FIXTURES = Path(__file__).parent / "data"


def _rich_facts():
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(FIXTURES),
    ).get_facts_for_match("flashscore_sample_league_match")
    assert facts is not None
    return facts


def test_classify_http_errors() -> None:
    assert classify_http_error_message("HTTP 401 for url") == "auth"
    assert classify_http_error_message("Request failed: timeout") == "timeout"
    assert classify_http_error_message("Invalid JSON from url") == "bad_payload"


def test_completeness_report_rich_flashscore() -> None:
    facts = _rich_facts()
    report = build_completeness_report(
        facts=facts,
        sources={"flashscore": "ok", "openclaw": "skipped", "odds": "skipped"},
        warnings=[],
        openclaw_ctx=None,
        odds_ctx=None,
    )
    assert report.flashscore_blocks.get("standings") is True
    assert report.flashscore_blocks.get("form") is True
    assert report.coverage_score() < 1.0
    assert report.openclaw_status == "skipped"


def test_telegram_hint_when_partial() -> None:
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(FIXTURES),
    ).get_facts_for_match("flashscore_botola_sample_match")
    assert facts is not None
    report = build_completeness_report(
        facts=facts,
        sources={"flashscore": "ok", "openclaw": "failed", "odds": "failed"},
        warnings=["openclaw_context_fetch_failed:timeout"],
        openclaw_ctx=None,
        odds_ctx=None,
    )
    hint = format_telegram_completeness_hint(report)
    assert hint is not None
    assert hint is not None
    assert "OpenClaw" in hint or "линия" in hint


def test_completeness_with_openclaw_fixture() -> None:
    facts = _rich_facts()
    oc = OpenClawContextIngestionService(
        FixtureFileOpenClawContextAdapter(FIXTURES),
    ).get_context_for_fixture("openclaw_context_sample")
    report = build_completeness_report(
        facts=facts,
        sources={"flashscore": "ok", "openclaw": "ok", "odds": "skipped"},
        warnings=[],
        openclaw_ctx=oc,
        odds_ctx=None,
    )
    assert report.openclaw_status == "ok"
    assert report.coverage_score() > 0.5
