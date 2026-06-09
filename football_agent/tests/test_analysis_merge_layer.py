"""Tests for analysis_merge: Flashscore facts + derived + optional OpenClaw context."""

from __future__ import annotations

from pathlib import Path

from football_agent.analysis_merge.merge import merge_flashscore_and_openclaw_context, merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService


FIXTURES_DIR = Path(__file__).parent / "data"


def _facts():
    svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))
    return svc.get_facts_for_match("flashscore_sample_league_match")


def _context():
    svc = OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(FIXTURES_DIR))
    return svc.get_context_for_fixture("openclaw_context_sample")


def _odds():
    svc = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES_DIR))
    return svc.get_odds_for_fixture("odds_sample")


def test_merge_with_context_links_by_teams_and_date() -> None:
    facts = _facts()
    ctx = _context()
    assert facts is not None and ctx is not None
    merged = merge_flashscore_and_openclaw_context(facts, ctx)
    assert merged.headline.openclaw_context_present is True
    assert merged.provenance.match_link_strategy in ("by_teams_and_date", "by_query_string")
    assert merged.headline.gap_to_title_points is not None


def test_merge_without_context_sets_warning() -> None:
    facts = _facts()
    assert facts is not None
    merged = merge_flashscore_and_openclaw_context(facts, None)
    assert merged.headline.openclaw_context_present is False
    assert "openclaw_context_not_provided" in merged.provenance.warnings
    assert "openclaw_context" in merged.provenance.missing_blocks
    # v2 adds odds warnings when odds are not provided.
    assert "odds_context_not_provided" in merged.provenance.warnings
    assert "odds_context" in merged.provenance.missing_blocks


def test_merge_provided_without_link_when_mismatch() -> None:
    facts = _facts()
    ctx = _context()
    assert facts is not None and ctx is not None
    # Force mismatch by changing query_string and teams
    ctx.meta.query_string = "something else"
    ctx.meta.query_home_team = "Other"
    ctx.meta.query_away_team = "Teams"
    ctx.meta.query_home_team_normalized = "other"
    ctx.meta.query_away_team_normalized = "teams"
    ctx.meta.query_date = None
    merged = merge_flashscore_and_openclaw_context(facts, ctx)
    assert merged.provenance.match_link_strategy in ("provided_without_link", "unlinked")


def test_merge_happy_path_with_context_and_odds_sets_headline_odds() -> None:
    facts = _facts()
    ctx = _context()
    odds = _odds()
    assert facts is not None and ctx is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=odds)
    assert merged.headline.openclaw_context_present is True
    assert merged.headline.odds_present is True
    assert merged.provenance.odds_link_strategy in ("by_match_id", "by_query_string", "by_teams_and_date", "provided_without_link")
    assert merged.headline.home_win_odds == 2.15
    assert merged.headline.away_win_odds == 3.4
    assert merged.headline.odds_missing_count >= 0


def test_merge_without_odds_sets_warning_and_missing_block() -> None:
    facts = _facts()
    ctx = _context()
    assert facts is not None and ctx is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=None)
    assert merged.headline.odds_present is False
    assert "odds_context_not_provided" in merged.provenance.warnings
    assert "odds_context" in merged.provenance.missing_blocks


def test_odds_provided_but_unlinked_does_not_fail_and_sets_strategy() -> None:
    facts = _facts()
    odds = _odds()
    assert facts is not None and odds is not None

    # Force mismatch: different teams + drop date so teams_and_date cannot match.
    odds.meta.home_team = "Other"
    odds.meta.away_team = "Teams"
    odds.meta.query_string = "something else"
    odds.meta.kickoff_utc = None

    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds)
    assert merged.odds_context is not None
    assert merged.provenance.odds_link_strategy in ("provided_without_link", "unlinked")
    assert "odds_provided_but_not_linked" in merged.provenance.warnings

