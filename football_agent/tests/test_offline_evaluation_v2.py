from __future__ import annotations

import json
from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.offline.evaluation_v2 import (
    SETTLEMENT_IDENTITY_CONTRACT,
    SLICE_NONE,
    SLICE_REPORT_MISSING,
    SLICE_UNKNOWN,
    build_evaluation_report_slices,
    evaluate_best_market_runs,
    extract_settlement_identity,
    resolve_match_result,
    settle_best_market,
)
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


def test_settlement_identity_contract_documented() -> None:
    assert "exact primary" in SETTLEMENT_IDENTITY_CONTRACT
    assert "normalized fallback" in SETTLEMENT_IDENTITY_CONTRACT


def test_extract_settlement_identity_from_snapshot_meta() -> None:
    identity = extract_settlement_identity(
        snapshot_json={
            "match_meta": {
                "match_date_utc": "2025-11-29T17:30:00Z",
                "home_team": {"name": "AC Milan"},
                "away_team": {"name": "Juventus"},
            }
        },
        run_home_team="Header Home",
        run_away_team="Header Away",
        run_kickoff_utc="2025-11-30T12:00:00Z",
    )
    assert identity is not None
    assert identity.match_date == "2025-11-29"
    assert identity.home_team == "AC Milan"
    assert identity.away_team == "Juventus"
    assert identity.source == "snapshot_meta"


def test_extract_settlement_identity_run_header_fallback() -> None:
    identity = extract_settlement_identity(
        snapshot_json={"match_meta": {}},
        run_home_team="AC Milan",
        run_away_team="Juventus",
        run_kickoff_utc="2025-11-29T17:30:00+00:00",
    )
    assert identity is not None
    assert identity.match_date == "2025-11-29"
    assert identity.home_team == "AC Milan"
    assert identity.away_team == "Juventus"
    assert identity.source == "run_header"


def test_resolve_match_result_exact_primary_when_sql_hit() -> None:
    """Exact SQL join wins even when normalized scan would be ambiguous."""
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "AC Milan", "away_team": "Juventus", "home_score": 2, "away_score": 1},
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
    assert st.resolved is True
    assert st.join_method == "exact"
    assert st.home_score == 2
    assert st.away_score == 1


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
    # No raw exact SQL match for prediction; two normalized-equivalent rows → unresolved.
    date_lookup, exact_lookup = _mock_lookups(
        {
            "2025-11-29": [
                {"match_date": "2025-11-29", "home_team": "ac milan", "away_team": "juventus", "home_score": 1, "away_score": 0},
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
                {"match_date": "2025-11-29", "home_team": "ac milan", "away_team": "juventus", "home_score": 1, "away_score": 0},
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


def test_offline_evaluation_settled_via_run_header_identity_fallback(tmp_path: Path) -> None:
    facts = _facts()
    assert facts is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    db_path = tmp_path / "eval_header.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)
    run_id = repo.create_run_from_merged(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=report)
    repo.attach_prediction(run_id, prediction=scored.prediction, scoring_warnings=list(scored.scoring_warnings))

    match_date = snapshot.match_meta.match_date_utc.date().isoformat()
    home = snapshot.match_meta.home_team.name
    away = snapshot.match_meta.away_team.name

    # Strip team names from snapshot JSON so evaluation must fall back to run header.
    snap_row = repo.conn.execute(
        "SELECT snapshot_json FROM analysis_snapshots_v2 WHERE id=(SELECT snapshot_id FROM analysis_runs_v2 WHERE run_id=?)",
        (run_id,),
    ).fetchone()
    assert snap_row is not None
    snap_data = json.loads(snap_row["snapshot_json"])
    snap_data["match_meta"]["home_team"] = {}
    snap_data["match_meta"]["away_team"] = {}
    repo.conn.execute(
        "UPDATE analysis_snapshots_v2 SET snapshot_json=? WHERE id=(SELECT snapshot_id FROM analysis_runs_v2 WHERE run_id=?)",
        (json.dumps(snap_data, ensure_ascii=False), run_id),
    )
    repo.conn.execute(
        "INSERT OR IGNORE INTO match_results(match_date, home_team, away_team, home_score, away_score, settled_at) VALUES (?,?,?,?,?,?)",
        (match_date, home, away, 1, 0, "2026-01-01T00:00:00Z"),
    )
    repo.conn.commit()
    repo.close()

    svc = OfflineEvaluationServiceV2(db_path=db_path)
    report_out = svc.evaluate(limit=50)
    svc.close()

    counts = report_out["counts"]
    assert counts["settled_runs_total"] >= 1
    assert counts["join_exact_count"] >= 1
    assert counts["join_unresolved_count"] == 0
    assert report_out["data_notes"]["settlement_identity_contract"] == SETTLEMENT_IDENTITY_CONTRACT


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


def test_build_evaluation_report_slices_from_artifacts() -> None:
    runs = [
        {
            "report": {
                "merge_missing_blocks": [],
                "openclaw_link_strategy": "by_teams_and_date",
                "odds_link_strategy": "by_match_id",
                "merge_warnings": ["merge_note"],
                "builder_warnings": [],
            },
            "scoring_warnings": ["low_confidence"],
            "competition_code": "SERIE_A",
            "competition_name": "Serie A",
        },
        {
            "report": {
                "merge_missing_blocks": ["odds_context", "openclaw_context"],
                "openclaw_link_strategy": "unlinked",
                "odds_link_strategy": "unlinked",
                "merge_warnings": [],
                "builder_warnings": ["flashscore_season_missing_used_kickoff_year"],
            },
            "scoring_warnings": [],
            "competition_code": None,
            "competition_name": None,
        },
        {
            "report": None,
            "scoring_warnings": [],
            "competition_code": "BOTOLA",
            "competition_name": "Botola Pro",
        },
    ]
    slices = build_evaluation_report_slices(runs, evaluable_runs_total=3, scored_runs_total=4)

    assert slices["meta"]["evaluable_runs_total"] == 3
    assert slices["meta"]["scored_runs_total"] == 4
    assert slices["meta"]["runs_with_report"] == 2
    assert slices["meta"]["runs_without_report"] == 1

    mb = slices["missing_blocks"]
    assert mb["by_signature"][SLICE_NONE] == 1
    assert mb["by_signature"]["odds_context+openclaw_context"] == 1
    assert mb["by_signature"][SLICE_REPORT_MISSING] == 1
    assert mb["by_block"]["odds_context"] == 1
    assert mb["by_block"]["openclaw_context"] == 1

    assert slices["openclaw_link_strategy"]["by_teams_and_date"] == 1
    assert slices["openclaw_link_strategy"]["unlinked"] == 1
    assert slices["openclaw_link_strategy"][SLICE_REPORT_MISSING] == 1

    assert slices["odds_link_strategy"]["by_match_id"] == 1
    assert slices["odds_link_strategy"]["unlinked"] == 1
    assert slices["odds_link_strategy"][SLICE_REPORT_MISSING] == 1

    warnings = slices["warnings"]
    assert warnings["runs_with_any_warning"] == 2
    assert warnings["runs_without_warnings"] == 1
    assert warnings["by_kind"]["merge_warnings"] == 1
    assert warnings["by_kind"]["builder_warnings"] == 1
    assert warnings["by_kind"]["scoring_warnings"] == 1

    assert slices["competition_code"]["SERIE_A"] == 1
    assert slices["competition_code"]["BOTOLA"] == 1
    assert slices["competition_code"][SLICE_UNKNOWN] == 1
    assert slices["competition_name"]["Serie A"] == 1
    assert slices["competition_name"]["Botola Pro"] == 1
    assert slices["competition_name"][SLICE_UNKNOWN] == 1


def test_evaluate_best_market_runs_includes_slices_without_changing_counts() -> None:
    date_lookup, exact_lookup = _mock_lookups({})
    runs = [
        {
            "match_date": "2025-11-29",
            "home_team": "AC Milan",
            "away_team": "Juventus",
            "best_market": None,
            "report": {
                "merge_missing_blocks": ["odds_context"],
                "openclaw_link_strategy": "by_teams_and_date",
                "odds_link_strategy": "unlinked",
                "merge_warnings": [],
                "builder_warnings": [],
            },
            "scoring_warnings": [],
            "competition_code": "SERIE_A",
            "competition_name": "Serie A",
        }
    ]
    report = evaluate_best_market_runs(
        runs,
        scored_runs_total=1,
        exact_lookup=exact_lookup,
        date_lookup=date_lookup,
    )
    assert report["counts"]["join_unresolved_count"] == 1
    assert report["counts"]["settled_runs_total"] == 0
    assert "slices" in report
    assert report["slices"]["missing_blocks"]["by_signature"]["odds_context"] == 1
    assert report["slices"]["openclaw_link_strategy"]["by_teams_and_date"] == 1


def test_offline_evaluation_slices_from_persisted_run(tmp_path: Path) -> None:
    facts = _facts()
    ctx = _ctx()
    odds = _odds()
    assert facts is not None and ctx is not None and odds is not None

    merged = merge_match_context_v2(facts=facts, openclaw_context=ctx, odds_context=odds)
    snapshot, build_report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, build_report)

    db_path = tmp_path / "eval_slices.db"
    repo = AnalysisRunRepositoryV2(db_path=db_path)
    run_id = repo.create_run_from_merged(merged)
    repo.attach_snapshot_and_report(run_id, merged=merged, snapshot=snapshot, report=build_report)
    repo.attach_prediction(run_id, prediction=scored.prediction, scoring_warnings=list(scored.scoring_warnings))
    repo.close()

    svc = OfflineEvaluationServiceV2(db_path=db_path)
    report_out = svc.evaluate(limit=50)
    svc.close()

    slices = report_out["slices"]
    assert slices["meta"]["evaluable_runs_total"] >= 1
    assert "openclaw_link_strategy" in slices
    assert "odds_link_strategy" in slices
    assert "missing_blocks" in slices
    assert "warnings" in slices
    assert "competition_code" in slices
    assert "competition_name" in slices

    total_link = sum(slices["openclaw_link_strategy"].values())
    assert total_link == slices["meta"]["evaluable_runs_total"]

