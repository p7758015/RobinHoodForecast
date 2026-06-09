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
from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.storage.v2_run_repository import AnalysisRunRepositoryV2


FIXTURES_DIR = Path(__file__).parent / "data"


def _facts():
    return FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR)).get_facts_for_match(
        "flashscore_sample_league_match"
    )


def _ctx():
    return OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(FIXTURES_DIR)).get_context_for_fixture(
        "openclaw_context_sample"
    )


def _odds():
    return OddsIngestionService(FixtureFileOddsAdapter(FIXTURES_DIR)).get_odds_for_fixture("odds_sample")


def test_offline_evaluation_settled_and_roi_subset_counts(tmp_path: Path) -> None:
    facts = _facts()
    ctx = _ctx()
    odds = _odds()
    assert facts is not None and ctx is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=odds)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    db_path = tmp_path / "eval.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)
    run_id = repo.create_run_from_merged(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=report)
    repo.attach_prediction(run_id, prediction=scored.prediction, scoring_warnings=list(scored.scoring_warnings))

    # Insert settled result using snapshot identity (exact join path).
    match_date = snapshot.match_meta.match_date_utc.date().isoformat()
    repo.conn.execute(
        "INSERT OR IGNORE INTO match_results(match_date, home_team, away_team, home_score, away_score, settled_at) VALUES (?,?,?,?,?,?)",
        (match_date, snapshot.match_meta.home_team.name, snapshot.match_meta.away_team.name, 1, 1, "2026-01-01T00:00:00Z"),
    )
    repo.conn.commit()
    repo.close()

    svc = OfflineEvaluationServiceV2(db_path=db_path)
    report_out = svc.evaluate(limit=50)
    svc.close()

    counts = report_out["counts"]
    assert counts["scored_runs"] >= 1
    assert counts["settled_runs"] >= 1
    # ROI subset is a strict subset; may be 0 if best_market has no book_odds, but must be present as a counter.
    assert "roi_subset" in counts


def test_offline_evaluation_unresolved_is_fail_soft(tmp_path: Path) -> None:
    facts = _facts()
    assert facts is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    db_path = tmp_path / "eval2.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)
    run_id = repo.create_run_from_merged(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=report)
    repo.attach_prediction(run_id, prediction=scored.prediction, scoring_warnings=list(scored.scoring_warnings))
    repo.close()

    svc = OfflineEvaluationServiceV2(db_path=db_path)
    report_out = svc.evaluate(limit=50)
    svc.close()
    assert report_out["counts"]["scored_runs"] >= 1
    assert report_out["counts"]["settled_runs"] == 0

