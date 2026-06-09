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
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.storage.match_key import build_match_key_from_merged
from football_agent.storage.v2_run_repository import AnalysisRunRepositoryV2


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


def test_run_status_transitions_and_latest_load(tmp_path: Path) -> None:
    facts = _facts()
    ctx = _context()
    odds = _odds()
    assert facts is not None and ctx is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=odds)
    match_key = build_match_key_from_merged(merged)

    db_path = tmp_path / "test_runs.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)

    run_id = repo.create_run_from_merged(merged)
    loaded0 = repo.load_run(run_id)
    assert loaded0 is not None
    assert loaded0.run_status == "merged_only"
    assert loaded0.match_key == match_key

    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=report)

    loaded1 = repo.load_run(run_id)
    assert loaded1 is not None
    assert loaded1.run_status == "snapshot_built"
    assert loaded1.snapshot is not None
    assert loaded1.build_report is not None

    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)
    repo.attach_prediction(run_id, prediction=scored.prediction, scoring_warnings=list(scored.scoring_warnings))

    loaded2 = repo.load_run(run_id)
    assert loaded2 is not None
    assert loaded2.run_status == "scored"
    assert loaded2.prediction is not None

    latest = repo.load_latest_run_for_match_key(match_key)
    assert latest is not None
    assert latest.run_id == run_id

    repo.close()


def test_build_report_has_match_key_index(tmp_path: Path) -> None:
    facts = _facts()
    assert facts is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    match_key = build_match_key_from_merged(merged)

    db_path = tmp_path / "test_runs2.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)
    run_id = repo.create_run_from_merged(merged)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=report)

    # direct query to ensure match_key persisted in build_reports table
    row = repo.conn.execute(
        "SELECT match_key FROM analysis_build_reports_v2 WHERE run_id=? ORDER BY created_at_utc DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    assert row is not None
    assert str(row["match_key"]) == match_key

    repo.close()

