"""Tests for OpenClaw context layer (fixture-based)."""

from __future__ import annotations

from pathlib import Path

from football_agent.debug.openclaw_context_trace import build_context_summary
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.openclaw_context.service import OpenClawContextIngestionService


FIXTURES_DIR = Path(__file__).parent / "data"


def _svc() -> OpenClawContextIngestionService:
    return OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(FIXTURES_DIR))


def test_fixture_maps_to_normalized_context() -> None:
    ctx = _svc().get_context_for_fixture("openclaw_context_sample")
    assert isinstance(ctx, OpenClawMatchContext)
    assert ctx.meta.query_home_team == "AC Milan"
    assert ctx.news is not None
    assert ctx.news.source_count is not None
    assert ctx.squad_context is not None
    assert ctx.coach_context is not None
    assert ctx.motivation_narrative is not None
    assert ctx.fatigue_schedule_context is not None


def test_partial_payload_does_not_crash_and_sets_missing_blocks() -> None:
    svc = _svc()
    raw = {
        "backend_name": "fixture",
        "query_home_team": "A",
        "query_away_team": "B",
        "collected_at_utc": "2025-01-01T00:00:00+00:00",
        # missing all optional blocks
    }
    ctx = svc._map_raw_to_context(raw)  # type: ignore[attr-defined]
    assert ctx.news is None
    assert "news" in ctx.provenance.missing_blocks
    assert "coach_context" in ctx.provenance.missing_blocks


def test_missing_optional_blocks_yield_none_and_warnings() -> None:
    svc = _svc()
    raw = {
        "backend_name": "fixture",
        "query_home_team": "A",
        "query_away_team": "B",
        "collected_at_utc": "2025-01-01T00:00:00+00:00",
        "news": {"home_news_items": []},
        "extraction_warnings": ["sample warning"],
    }
    ctx = svc._map_raw_to_context(raw)  # type: ignore[attr-defined]
    assert ctx.provenance.extraction_warnings == ["sample warning"]
    assert ctx.news is not None
    assert ctx.news.source_count == 0


def test_debug_summary_does_not_crash() -> None:
    ctx = _svc().get_context_for_fixture("openclaw_context_sample")
    assert ctx is not None
    summary = build_context_summary(ctx)
    assert summary["meta"]["query_home_team"] == "AC Milan"
    assert "news" in summary

