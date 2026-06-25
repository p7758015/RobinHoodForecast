"""Tests for league eval-pool accumulation, settlement, and reporting workflow."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.domain.models_v2 import MatchPredictionResultV2, TeamScoringResultV2
from football_agent.eval_pool.accumulate import accumulate_league_pool
from football_agent.eval_pool.report import LeagueEvalPoolReporter
from football_agent.eval_pool.scope import resolve_pool_entry
from football_agent.eval_pool.settle import (
    extract_final_score,
    is_finished_status,
    settle_league_pool_from_flashscore,
)
from football_agent.flashscore.models import FlashscoreMeta
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures
from football_agent.scorers.routing import ScorerRoutingDecision
from football_agent.services.competition_classifier import classify_competition_meta
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.tests.test_scorer_v2 import HOME, AWAY, make_snapshot

FIXTURES = Path(__file__).parent / "data"


@pytest.mark.parametrize(
    "competition_name,country,expected_key",
    [
        ("Premier League", "Kazakhstan", "kazakhstan_premier"),
        ("Meistriliiga", "Estonia", "estonia_meistriliiga"),
        ("Meistriliiga Women", "Estonia", "estonia_premium_liiga"),
        ("Premium Liiga", "Estonia", "estonia_premium_liiga"),
        ("Virsliga", "Latvia", "latvia_virsliga"),
        ("Brazil Serie B", "Brazil", "brazil_serie_b"),
    ],
)
def test_wave1_pool_scope_resolution(competition_name: str, country: str, expected_key: str) -> None:
    entry = resolve_pool_entry(competition_name, country)
    assert entry is not None
    assert entry.key == expected_key

    meta = FlashscoreMeta(
        match_id="t1",
        source_url="https://example.com",
        competition_name=competition_name,
        competition_country=country,
        home_team_name="Home FC",
        away_team_name="Away FC",
    )
    clf = classify_competition_meta(meta)
    assert clf.is_league_eligible is True
    assert clf.category == CompetitionContextClass.LEAGUE


def test_italian_serie_b_not_in_brazil_pool() -> None:
    assert resolve_pool_entry("Serie B", "Italy") is None
    entry = resolve_pool_entry("Serie B", "Brazil")
    assert entry is not None
    assert entry.key == "brazil_serie_b"


def test_settle_extract_score_and_status() -> None:
    assert is_finished_status("FT") is True
    assert is_finished_status("SCHEDULED") is False
    assert extract_final_score({"home_score": 2, "away_score": 1}) == (2, 1)
    assert extract_final_score({"score": {"home": 3, "away": 0}}) == (3, 0)


def test_settle_league_pool_saves_match_results() -> None:
    raw_finished = {
        "match_id": "lv-1",
        "competition_name": "Virsliga",
        "competition_country": "Latvia",
        "home_team_name": "Riga FC",
        "away_team_name": "Valmiera",
        "kickoff_utc": "2026-06-01T16:00:00+00:00",
        "status": "FT",
        "home_score": 2,
        "away_score": 0,
    }
    raw_other = {
        "match_id": "wc-1",
        "competition_name": "FIFA World Cup",
        "competition_country": "World",
        "home_team_name": "Brazil",
        "away_team_name": "France",
        "status": "FT",
        "home_score": 1,
        "away_score": 1,
    }

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "settle.db"
        summary = settle_league_pool_from_flashscore(
            date_from="2026-06-01",
            date_to="2026-06-01",
            league_keys=["latvia_virsliga"],
            db_path=db_path,
            fetch_matches_for_date=lambda _d: [raw_finished, raw_other],
        )
        assert summary["results_saved"] == 1
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT home_score, away_score FROM match_results WHERE home_team='Riga FC'"
        ).fetchone()
        conn.close()
        assert row == (2, 0)


@dataclass
class _FakePipeline:
    persist_ok: bool = True
    route: str = "league_full"
    odds: str = "partial"
    confidence: float = 0.62

    def analyze_flashscore_url(self, match_url: str, **kwargs) -> LivePipelineResult:
        snap = make_snapshot()
        pred = MatchPredictionResultV2(
            match_meta=snap.match_meta,
            home_scoring=TeamScoringResultV2(team=HOME),
            away_scoring=TeamScoringResultV2(team=AWAY),
            overall_confidence_score=self.confidence,
        )
        from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport

        scored = ScoredRunV2(
            snapshot=snap,
            prediction=pred,
            build_report=BuildReport(),
            routing_decision=ScorerRoutingDecision(
                route=self.route,  # type: ignore[arg-type]
                tournament_type=TournamentType.LEAGUE_REGULAR,
                category=CompetitionContextClass.LEAGUE,
                classification_confidence="high",
                league_eligible=self.route == "league_full",
                reason="test",
            ),
        )
        return LivePipelineResult(
            success=True,
            path="flashscore_url",
            scored_run=scored,
            run_id="run-test-1" if self.persist_ok else None,
            persisted=self.persist_ok,
            sources={"odds": self.odds, "flashscore": "ok"},
            routing_decision=scored.routing_decision,
        )


def test_accumulate_skips_parked_and_out_of_scope() -> None:
    kz = {
        "match_id": "kz-1",
        "source_url": "https://flashscore.com/match/?mid=kz-1",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "A",
        "away_team_name": "B",
        "status": "SCHEDULED",
    }
    wc = {
        "match_id": "wc-1",
        "competition_name": "FIFA World Cup",
        "competition_country": "World",
        "home_team_name": "Brazil",
        "away_team_name": "France",
        "status": "SCHEDULED",
    }

    calls: List[str] = []

    class _TrackingPipeline(_FakePipeline):
        def analyze_flashscore_url(self, match_url: str, **kwargs) -> LivePipelineResult:
            calls.append(match_url)
            return super().analyze_flashscore_url(match_url)

    summary = accumulate_league_pool(
        date_from="2026-06-02",
        date_to="2026-06-02",
        league_keys=["kazakhstan_premier"],
        fetch_matches_for_date=lambda _d: [kz, wc],
        pipeline_factory=lambda: _TrackingPipeline(),
    )
    assert summary["fixtures_in_scope"] == 1
    assert summary["league_full_scored"] == 1
    assert len(calls) == 1


def test_accumulate_tolerates_partial_odds_and_pipeline_errors() -> None:
    kz = {
        "match_id": "kz-2",
        "source_url": "https://flashscore.com/match/?mid=kz-2",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "C",
        "away_team_name": "D",
    }
    kz_bad = {
        "match_id": "kz-3",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "E",
        "away_team_name": "F",
    }

    class _MixedPipeline:
        def __init__(self) -> None:
            self._n = 0

        def analyze_flashscore_url(self, match_url: str, **kwargs) -> LivePipelineResult:
            self._n += 1
            if "kz-3" in match_url or self._n == 2:
                return LivePipelineResult(success=False, path="flashscore_url", stage_failed="fetch")
            return _FakePipeline(odds="partial", confidence=0.40).analyze_flashscore_url(match_url)

    summary = accumulate_league_pool(
        date_from="2026-06-02",
        date_to="2026-06-02",
        league_keys=["kazakhstan_premier"],
        fetch_matches_for_date=lambda _d: [kz, kz_bad],
        pipeline_factory=lambda: _MixedPipeline(),
    )
    assert summary["league_full_scored"] == 1
    assert summary["runs_with_odds"] == 1
    assert summary["low_confidence_runs"] == 1
    assert summary["pipeline_fail"] >= 1


def test_report_excludes_out_of_pool_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "pool_report.db"
    league_item = {
        "flashscore_stem": "flashscore_sample_league_match",
        "openclaw_stem": "openclaw_context_sample",
        "odds_stem": "odds_sample",
        "home_score": 2,
        "away_score": 1,
    }
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [league_item],
        db_path=db_path,
        save_match_results=True,
    )

    reporter = LeagueEvalPoolReporter(db_path=db_path)
    try:
        report = reporter.build_report(league_keys=["kazakhstan_premier"])
        assert report["counts"]["pool_runs"] == 0
        report_serie = reporter.build_report(league_keys=["brazil_serie_b"])
        assert report_serie["counts"]["pool_runs"] == 0
    finally:
        reporter.close()


def test_report_separates_parked_analysis_only_in_pool(tmp_path: Path) -> None:
    db_path = tmp_path / "parked_pool.db"
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [
            {
                "flashscore_stem": "flashscore_kazakhstan_premier_match",
                "home_score": 1,
                "away_score": 1,
            }
        ],
        db_path=db_path,
        save_match_results=True,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE analysis_predictions_v2
           SET prediction_json = json_set(prediction_json, '$.analysis_mode', 'analysis_only')
        """
    )
    conn.commit()
    conn.close()

    reporter = LeagueEvalPoolReporter(db_path=db_path)
    try:
        report = reporter.build_report(league_keys=["kazakhstan_premier"])
        assert report["counts"]["pool_runs"] == 1
        assert report["counts"]["parked_or_analysis_only_in_pool"] == 1
        assert report["counts"]["league_scored_runs"] == 0
    finally:
        reporter.close()


def test_kazakhstan_fixture_classified_and_persistable(tmp_path: Path) -> None:
    """Regression: Kazakhstan Premier League fixture goes through batch persist path."""
    db_path = tmp_path / "kz.db"
    out = run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [
            {
                "flashscore_stem": "flashscore_kazakhstan_premier_match",
                "home_score": 1,
                "away_score": 0,
            }
        ],
        db_path=db_path,
        save_match_results=True,
    )
    assert out["runs_persisted"] == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT competition_code, run_status FROM analysis_runs_v2"
    ).fetchone()
    pred = conn.execute(
        "SELECT prediction_json FROM analysis_predictions_v2 LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[1] == "scored"
    payload = json.loads(pred[0])
    assert payload.get("analysis_mode") != "analysis_only"
