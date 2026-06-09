from __future__ import annotations

from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.services.scoring_service_v2 import ScoringServiceV2, ScoredRunV2
from football_agent.tests.test_scorer_v2 import make_snapshot


FIXTURES_DIR = Path(__file__).parent / "data"


def test_score_snapshot_convenience_wrapper() -> None:
    snapshot = make_snapshot(with_odds=False)
    pred = ScoringServiceV2().score_snapshot(snapshot)
    assert pred.best_market is not None


def test_score_snapshot_with_report_wraps_without_mutation() -> None:
    # minimal snapshot + empty report
    snapshot = make_snapshot(with_odds=True)

    # BuildReport is in normalizers.merged_snapshot_builder_v2; import directly to avoid coupling here.
    from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport

    report = BuildReport(merge_warnings=["x"], merge_missing_blocks=["odds_context"])
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)
    assert isinstance(scored, ScoredRunV2)
    assert scored.snapshot is snapshot
    assert scored.build_report is report

    # Warnings: transparent aggregation of existing scorer-side reasons.
    assert scored.scoring_warnings == list(scored.prediction.express_safety.reasons or [])


def test_fixture_e2e_merge_builder_scorer_full_path() -> None:
    fs = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))
    facts = fs.get_facts_for_match("flashscore_sample_league_match")
    assert facts is not None

    oc = OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(FIXTURES_DIR))
    oc_ctx = oc.get_context_for_fixture("openclaw_context_sample")
    assert oc_ctx is not None

    odds = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES_DIR))
    odds_ctx = odds.get_odds_for_fixture("odds_sample")
    assert odds_ctx is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=oc_ctx, odds_context=odds_ctx)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    assert scored.prediction.best_market is not None
    assert scored.build_report.openclaw_link_strategy in (
        "by_match_id",
        "by_query_string",
        "by_teams_and_date",
        "provided_without_link",
        "unlinked",
    )
    assert scored.build_report.odds_link_strategy in (
        "by_match_id",
        "by_query_string",
        "by_teams_and_date",
        "provided_without_link",
        "unlinked",
    )


def test_fixture_e2e_missing_blocks_do_not_crash() -> None:
    fs = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))
    facts = fs.get_facts_for_match("flashscore_sample_league_match")
    assert facts is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    assert scored.prediction is not None
    assert "openclaw_context" in scored.build_report.merge_missing_blocks
    assert "odds_context" in scored.build_report.merge_missing_blocks

