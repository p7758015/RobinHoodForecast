from __future__ import annotations

from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport, MergedSnapshotBuilderV2
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


def test_builder_happy_path_with_openclaw_and_odds() -> None:
    facts = _facts()
    ctx = _context()
    odds = _odds()
    assert facts is not None and ctx is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=odds)
    builder = MergedSnapshotBuilderV2()
    snap, report = builder.build_with_report(merged)

    assert snap.match_meta.competition_name == "Serie A"
    assert snap.match_meta.match_date_utc is not None
    assert snap.match_meta.home_team.name == "AC Milan"
    assert snap.match_meta.away_team.name == "Juventus"

    # odds mapped into snapshot fields that exist in OddsContextV2
    assert snap.odds.home_win is not None
    assert snap.odds.away_win is not None
    assert snap.odds.home_not_lose is not None
    assert snap.odds.away_not_lose is not None
    assert snap.odds.over_15 is not None

    assert snap.home_team_context.form.last_5_form_score != 0.5
    assert snap.home_team_context.motivation.league_position == 3
    assert snap.home_schedule.days_since_last_match == 5
    assert snap.home_coach.coach.name == "Coach Home"
    assert snap.confidence.teams_confidence >= 0.6
    assert snap.confidence.overall_completeness_score != 0.5

    assert isinstance(report, BuildReport)
    assert report.openclaw_link_strategy in ("by_match_id", "by_query_string", "by_teams_and_date", "provided_without_link", "unlinked")
    assert report.odds_link_strategy in ("by_match_id", "by_query_string", "by_teams_and_date", "provided_without_link", "unlinked")
    assert "match_id" in report.id_generation_notes
    # Either real numeric id extracted or synthesized compatibility id.
    assert report.id_generation_notes["match_id"] in (
        "used_numeric_substring_from_flashscore_match_id",
        "synthesized_crc32_from_flashscore_match_id_for_snapshot_compat_only",
    )


def test_builder_without_openclaw_is_fail_soft() -> None:
    facts = _facts()
    odds = _odds()
    assert facts is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)

    assert snap.news_context is not None
    assert len(snap.news_context.major_news_items) == 0
    assert "openclaw_context" in report.merge_missing_blocks


def test_builder_without_odds_is_fail_soft() -> None:
    facts = _facts()
    ctx = _context()
    assert facts is not None and ctx is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=None)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)

    assert snap.odds is not None
    assert snap.odds.home_win is None
    assert "odds_context" in report.merge_missing_blocks


def test_build_from_merged_is_thin_wrapper() -> None:
    facts = _facts()
    assert facts is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    builder = MergedSnapshotBuilderV2()

    snap1 = builder.build_from_merged(merged)
    snap2, _report = builder.build_with_report(merged)
    assert snap1.match_meta.match_id == snap2.match_meta.match_id

