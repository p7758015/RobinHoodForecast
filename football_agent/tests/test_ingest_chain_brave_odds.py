"""Brave trace contract + odds bridge + schedule edge cases for ingest_chain_trace."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from football_agent.collectors.contracts import BLOCK_ODDS, MatchCollectionBundle, MatchRef
from football_agent.flashscore.models import (
    FlashscoreMatchFacts,
    FlashscoreMeta,
    FlashscoreProvenance,
    FlashscoreScheduleRaw,
)
from football_agent.news_context.models import CoachContextBlock, GeneralNewsBlock, MatchNewsContext
from football_agent.normalizers.flashscore_snapshot_helpers import schedule_context_from_raw
from football_agent.services.openclaw_news_enrichment import (
    classify_match_news_enrichment_status,
    summarize_brave_news_context,
)


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="lEgK7KgC",
            source_url="https://example.invalid/m1",
            home_team_name="America MG",
            away_team_name="Criciuma",
            competition_name="Serie B",
            competition_country="Brazil",
            kickoff_utc=datetime(2026, 6, 24, 2, 0, tzinfo=timezone.utc),
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def test_classify_brave_status_no_crash_on_coach_block_fields() -> None:
    news = MatchNewsContext(
        coach=CoachContextBlock(home_coach_name="Coach A", away_coach_name="Coach B"),
        general_news=GeneralNewsBlock(injuries_signals=["striker doubt"]),
        source_count=3,
        confidence=0.55,
    )
    assert classify_match_news_enrichment_status(news) == "success_useful"
    summary = summarize_brave_news_context(news)
    assert summary["coach_home"] == "Coach A"
    assert summary["injuries"] == ["striker doubt"]


def test_classify_brave_parsing_error() -> None:
    assert (
        classify_match_news_enrichment_status(None, error="'CoachContextBlock' has no attribute 'home'")
        == "parsing_error"
    )


def test_schedule_days_to_next_null_when_next_before_kickoff() -> None:
    sched = FlashscoreScheduleRaw(
        previous_match_date_home=date(2026, 6, 16),
        next_match_date_home=date(2026, 6, 23),
    )
    kickoff = datetime(2026, 6, 24, 2, 0, tzinfo=timezone.utc)
    ctx = schedule_context_from_raw(sched, kickoff, side="home")
    assert ctx.days_since_last_match == 8
    assert ctx.days_to_next_match is None
    assert ctx.matches_next_7_days == 0


@patch("football_agent.debug.ingest_chain_trace.HttpFlashscoreScraperAdapter")
@patch("football_agent.debug.ingest_chain_trace.FlashscoreIngestionService")
@patch("football_agent.debug.ingest_chain_trace.MatchCollectorOrchestrator")
def test_ingest_chain_trace_brave_and_odds_wiring(
    mock_orch: MagicMock,
    mock_fs_svc: MagicMock,
    mock_adapter: MagicMock,
) -> None:
    from football_agent.collectors.contracts import BlockCollectionResult
    from football_agent.debug.ingest_chain_trace import run_live_chain

    raw = {
        "match_id": "lEgK7KgC",
        "home_team_name": "America MG",
        "away_team_name": "Criciuma",
        "competition_name": "Serie B",
        "kickoff_utc": "2026-06-24T02:00:00+00:00",
    }
    mock_adapter.return_value.fetch_match_raw.return_value = raw
    facts = _facts()
    mock_fs_svc.return_value.get_facts_for_match.return_value = facts

    from football_agent.collectors.contracts import BLOCK_ODDS, BlockCollectionResult

    odds_block = BlockCollectionResult(
        block=BLOCK_ODDS,
        status="ok",
        confidence=0.95,
        source="flashscore_collector",
        collected_at_utc=datetime.now(timezone.utc),
        payload={
            "bookmaker": "BetMGM.us",
            "markets": {
                "HOME_WIN": {"value": 2.8, "raw_label": "home_win"},
                "AWAY_WIN": {"value": 2.45, "raw_label": "away_win"},
                "HOME_OR_DRAW": {"value": 1.5, "raw_label": "double_chance_1x"},
                "AWAY_OR_DRAW": {"value": 1.39, "raw_label": "double_chance_x2"},
                "BTTS_YES": {"value": 2.0, "raw_label": "btts_yes"},
                "OVER_1_5": {"value": 1.44, "raw_label": "over_1_5"},
            },
        },
    )
    bundle = MatchCollectionBundle(
        match_key="lEgK7KgC",
        match_ref=MatchRef(match_id="lEgK7KgC", home_team="America MG", away_team="Criciuma"),
        blocks={BLOCK_ODDS: odds_block},
        overall_confidence=0.9,
        overall_status="ok",
    )
    mock_orch.return_value.collect_from_raw.return_value = (bundle, MagicMock())

    news = MatchNewsContext(
        coach=CoachContextBlock(home_coach_name="Técnico Home"),
        general_news=GeneralNewsBlock(),
        source_count=2,
        confidence=0.4,
    )

    with patch(
        "football_agent.debug.ingest_chain_trace.enrich_http_flashscore_raw",
        side_effect=lambda r: r,
    ), patch(
        "football_agent.debug.ingest_chain_trace.apply_bundle_to_facts",
        side_effect=lambda f, b: (f, []),
    ), patch(
        "football_agent.services.openclaw_news_enrichment.enrich_match_news_from_brave",
        return_value=news,
    ), patch(
        "football_agent.services.openclaw_news_enrichment.brave_news_enabled",
        return_value=True,
    ), patch(
        "football_agent.services.openclaw_news_enrichment.brave_coach_context_enabled",
        return_value=True,
    ):
        pkg = run_live_chain("https://example.invalid/m1", scraper_url="http://localhost:3000", with_brave=True)

    report = pkg["report"]
    assert report["meta"]["brave"]["coach_home"] == "Técnico Home"
    assert report["meta"]["brave"]["items"] == 2
    assert report["meta"]["brave"]["outcome"] in ("success_partial", "success_useful")
    snap_odds = report["layers"]["snapshot"]["odds"]
    assert snap_odds["home_win"] == 2.8
    assert snap_odds["away_not_lose"] == 1.39
