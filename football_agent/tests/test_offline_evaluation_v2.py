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
from football_agent.offline.evaluation_v2 import evaluate_best_market_runs, resolve_match_result, settle_best_market
from football_agent.offline.market_outcomes import V2_MARKET_KEYS, evaluate_market_outcome, v2_market_is_win
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


def _mock_lookups(rows_by_date: dict):
    def date_lookup(match_date: str):
        return list(rows_by_date.get(match_date, []))

    def exact_lookup(match_date: str, home_team: str, away_team: str):
        for row in rows_by_date.get(match_date, []):
            if row["home_team"] == home_team and row["away_team"] == away_team:
                return row
        return None

    return date_lookup, exact_lookup


def _eval_one_run(match_date: str, home: str, away: str, *, date_lookup, exact_lookup) -> dict:
    return evaluate_best_market_runs(
        [{"match_date": match_date, "home_team": home, "away_team": away, "best_market": None}],
        scored_runs_total=1,
        exact_lookup=exact_lookup,
        date_lookup=date_lookup,
    )


_SETTLEMENT_SYNC_CASES = [
    # HOME_WIN
    ("HOME_WIN", 2, 1, True),
    ("HOME_WIN", 1, 1, False),
    ("HOME_WIN", 0, 2, False),
    # AWAY_WIN
    ("AWAY_WIN", 1, 2, True),
    ("AWAY_WIN", 1, 1, False),
    ("AWAY_WIN", 3, 0, False),
    # HOME_NOT_LOSE (1X)
    ("HOME_NOT_LOSE", 2, 1, True),
    ("HOME_NOT_LOSE", 1, 1, True),
    ("HOME_NOT_LOSE", 0, 1, False),
    # AWAY_NOT_LOSE (X2)
    ("AWAY_NOT_LOSE", 1, 2, True),
    ("AWAY_NOT_LOSE", 1, 1, True),
    ("AWAY_NOT_LOSE", 2, 0, False),
    # BTTS_YES
    ("BTTS_YES", 1, 1, True),
    ("BTTS_YES", 1, 0, False),
    ("BTTS_YES", 0, 0, False),
    # HOME_TEAM_TO_SCORE
    ("HOME_TEAM_TO_SCORE", 1, 0, True),
    ("HOME_TEAM_TO_SCORE", 0, 2, False),
    ("HOME_TEAM_TO_SCORE", 0, 0, False),
    # AWAY_TEAM_TO_SCORE
    ("AWAY_TEAM_TO_SCORE", 0, 1, True),
    ("AWAY_TEAM_TO_SCORE", 2, 0, False),
    ("AWAY_TEAM_TO_SCORE", 0, 0, False),
    # OVER_1_5
    ("OVER_1_5", 1, 1, True),
    ("OVER_1_5", 2, 0, True),
    ("OVER_1_5", 1, 0, False),
]


def test_settlement_logic_sync_run_level_and_flat() -> None:
    for market_key, hs, as_, expected in _SETTLEMENT_SYNC_CASES:
        flat = v2_market_is_win(market_key, hs, as_)
        canonical = evaluate_market_outcome(market_key, hs, as_)
        run_level = settle_best_market(market_key, hs, as_)
        assert flat == expected, f"v2_market_is_win mismatch for {market_key} {hs}-{as_}"
        assert canonical == expected
        assert run_level == expected, f"settle_best_market mismatch for {market_key} {hs}-{as_}"
        assert flat == canonical == run_level

    covered = {c[0] for c in _SETTLEMENT_SYNC_CASES}
    assert covered == set(V2_MARKET_KEYS)

    assert evaluate_market_outcome("UNKNOWN_MARKET", 1, 0) is None
    assert v2_market_is_win("UNKNOWN_MARKET", 1, 0) is None
    assert settle_best_market("UNKNOWN_MARKET", 1, 0) is None


def test_resolve_match_result_exact_join() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "AC Milan", "away_team": "Juventus", "home_score": 2, "away_score": 1},
            ],
        }
    )
    st = resolve_match_result(
        match_date="2025-11-29",
        home_team="AC Milan",
        away_team="Juventus",
        exact_lookup=exact_lookup,
        date_lookup=date_lookup,
    )
    assert st.resolved is True
    assert st.join_method == "exact"
    assert st.home_score == 2
    assert st.away_score == 1


def test_resolve_match_result_normalized_join() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "ac milan", "away_team": "juventus", "home_score": 1, "away_score": 1},
            ],
        }
    )
    st = resolve_match_result(
        match_date="2025-11-29",
        home_team="AC Milan",
        away_team="Juventus",
        exact_lookup=exact_lookup,
        date_lookup=date_lookup,
    )
    assert st.resolved is True
    assert st.join_method == "normalized"


def test_resolve_match_result_ambiguous_normalized_hits() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "AC Milan", "away_team": "Juventus", "home_score": 1, "away_score": 0},
                {"match_date": "2025-11-29", "home_team": "AC  Milan", "away_team": "Juventus", "home_score": 0, "away_score": 0},
            ],
        }
    )
    st = resolve_match_result(
        match_date="2025-11-29",
        home_team="AC Milan",
        away_team="Juventus",
        exact_lookup=exact_lookup,
        date_lookup=date_lookup,
    )
    assert st.resolved is False
    assert st.join_method == "unresolved"


def test_evaluate_join_exact_counts() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "AC Milan", "away_team": "Juventus", "home_score": 1, "away_score": 1},
            ],
        }
    )
    report = _eval_one_run("2025-11-29", "AC Milan", "Juventus", date_lookup=date_lookup, exact_lookup=exact_lookup)
    counts = report["counts"]
    assert counts["join_exact_count"] == 1
    assert counts["join_normalized_count"] == 0
    assert counts["join_unresolved_count"] == 0
    assert counts["settled_runs_total"] == 1
    assert report["metrics"]["settled_coverage"] == 1.0


def test_evaluate_join_normalized_counts() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "ac milan", "away_team": "juventus", "home_score": 2, "away_score": 0},
            ],
        }
    )
    report = _eval_one_run("2025-11-29", "AC Milan", "Juventus", date_lookup=date_lookup, exact_lookup=exact_lookup)
    counts = report["counts"]
    assert counts["join_exact_count"] == 0
    assert counts["join_normalized_count"] == 1
    assert counts["join_unresolved_count"] == 0
    assert counts["settled_runs_total"] == 1


def test_evaluate_join_ambiguous_unresolved() -> None:
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "AC Milan", "away_team": "Juventus", "home_score": 1, "away_score": 0},
                {"match_date": "2025-11-29", "home_team": "AC  Milan", "away_team": "Juventus", "home_score": 0, "away_score": 1},
            ],
        }
    )
    report = _eval_one_run("2025-11-29", "AC Milan", "Juventus", date_lookup=date_lookup, exact_lookup=exact_lookup)
    counts = report["counts"]
    assert counts["evaluable_runs_total"] == 1
    assert counts["join_unresolved_count"] == 1
    assert counts["join_exact_count"] == 0
    assert counts["join_normalized_count"] == 0
    assert counts["settled_runs_total"] == 0
    assert report["metrics"]["settled_coverage"] == 0.0


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
    metrics = report_out["metrics"]

    assert counts["scored_runs_total"] >= 1
    assert counts["evaluable_runs_total"] >= 1
    assert counts["settled_runs_total"] >= 1
    assert counts["skipped_identity_runs_total"] == 0
    assert counts["join_exact_count"] == 1
    assert counts["join_normalized_count"] == 0
    assert counts["join_unresolved_count"] == 0
    assert counts["scored_runs"] == counts["scored_runs_total"]
    assert counts["settled_runs"] == counts["settled_runs_total"]

    assert metrics["evaluable_coverage"] == round(
        counts["evaluable_runs_total"] / counts["scored_runs_total"], 4
    )
    assert metrics["settled_coverage"] == round(
        counts["settled_runs_total"] / counts["evaluable_runs_total"], 4
    )
    assert metrics["settled_coverage"] == 1.0
    assert "odds_coverage" in metrics
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
    counts = report_out["counts"]
    metrics = report_out["metrics"]

    assert counts["scored_runs_total"] >= 1
    assert counts["evaluable_runs_total"] >= 1
    assert counts["settled_runs_total"] == 0
    assert counts["skipped_identity_runs_total"] == 0
    assert metrics["settled_coverage"] == 0.0
    assert metrics["evaluable_coverage"] > 0.0
    assert counts["join_unresolved_count"] >= 1
    assert counts["join_exact_count"] == 0
    assert counts["join_normalized_count"] == 0

