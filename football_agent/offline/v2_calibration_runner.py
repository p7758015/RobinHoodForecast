"""
Offline batch tooling for v2 calibration.

Paths:
- **Flat (legacy):** finished matches -> MatchSnapshotBuilder -> LeagueScorerV2 -> ``v2_predictions``
- **Run-level (Stage 1):** fixtures -> merge -> MergedSnapshotBuilderV2 -> ScoringServiceV2
  -> ``SnapshotPersistenceServiceV2`` -> ``analysis_*_v2`` tables
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.config import API_FOOTBALL_KEY, FOOTBALL_DATA_API_KEY
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.models import Match
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2
from football_agent.services.persistence_service_v2 import SnapshotPersistenceServiceV2
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.storage.v2_database import V2Database

logger = logging.getLogger(__name__)


def _filter_competition(matches: List[Match], competition_code: Optional[str]) -> List[Match]:
    if not competition_code:
        return matches
    code = competition_code.upper()
    return [m for m in matches if m.competition_code.upper() == code]


def run_v2_for_date(
    date_str: str,
    competition_code: Optional[str] = None,
    *,
    fd: Optional[FootballDataClient] = None,
    af: Optional[ApiFootballClient] = None,
    db: Optional[V2Database] = None,
    write_flat_predictions: bool = True,
) -> dict:
    """
    Score finished matches for a date and optionally store flat v2 market rows.
    Returns run summary counters.
    """
    fd = fd or FootballDataClient(FOOTBALL_DATA_API_KEY or "")
    af = af or ApiFootballClient(API_FOOTBALL_KEY or "")
    db = db or V2Database()
    builder = MatchSnapshotBuilder(fd, af)
    scorer = LeagueScorerV2()

    matches = _filter_competition(fd.get_finished_matches_by_date(date_str), competition_code)
    logger.info(
        "v2 calibration run: date=%s competition=%s finished_matches=%d",
        date_str,
        competition_code or "ALL",
        len(matches),
    )

    summary = {
        "date": date_str,
        "competition": competition_code,
        "matches_found": len(matches),
        "matches_scored": 0,
        "rows_inserted": 0,
        "skipped": 0,
        "write_flat_predictions": write_flat_predictions,
    }

    for match in matches:
        if match.home_score is None or match.away_score is None:
            summary["skipped"] += 1
            continue
        try:
            db.save_match_result(
                date_str,
                match.home_team.name,
                match.away_team.name,
                int(match.home_score),
                int(match.away_score),
            )
            snapshot = builder.build_snapshot_for_match(match)
            prediction = scorer.score_snapshot(snapshot)
            rows = 0
            if write_flat_predictions:
                rows = db.save_prediction_result(
                    prediction,
                    date_str,
                    h2h_btts_rate=snapshot.h2h_context.h2h_btts_rate,
                )
            summary["matches_scored"] += 1
            summary["rows_inserted"] += rows
        except Exception as e:
            summary["skipped"] += 1
            logger.exception(
                "v2 calibration skip match %s %s vs %s: %s",
                match.id,
                match.home_team.name,
                match.away_team.name,
                e,
            )

    logger.info("v2 calibration done: %s", summary)
    return summary


def run_v2_for_date_range(
    start_date: str,
    end_date: str,
    competition_code: Optional[str] = None,
    *,
    write_flat_predictions: bool = True,
) -> List[dict]:
    """Iterate inclusive date range (YYYY-MM-DD)."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be >= start_date")

    fd = FootballDataClient(FOOTBALL_DATA_API_KEY or "")
    af = ApiFootballClient(API_FOOTBALL_KEY or "")
    db = V2Database()

    summaries: List[dict] = []
    current = start
    while current <= end:
        summaries.append(
            run_v2_for_date(
                current.isoformat(),
                competition_code,
                fd=fd,
                af=af,
                db=db,
                write_flat_predictions=write_flat_predictions,
            )
        )
        current += timedelta(days=1)
    return summaries


def _persist_one_fixture_match(
    *,
    fixtures_dir: Path,
    flashscore_stem: str,
    openclaw_stem: Optional[str],
    odds_stem: Optional[str],
    persister: SnapshotPersistenceServiceV2,
    db: V2Database,
    save_match_results: bool,
    home_score: int,
    away_score: int,
) -> Dict[str, Any]:
    """Merge -> snapshot -> score -> persist one fixture match into run-level storage."""
    fs_svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(fixtures_dir))
    facts = fs_svc.get_facts_for_match(flashscore_stem)
    if facts is None:
        raise ValueError(f"Flashscore fixture not found: {flashscore_stem}")

    oc_ctx = None
    if openclaw_stem:
        oc_svc = OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(fixtures_dir))
        oc_ctx = oc_svc.get_context_for_fixture(openclaw_stem)

    odds_ctx = None
    if odds_stem:
        odds_svc = OddsIngestionService(FixtureFileOddsAdapter(fixtures_dir))
        odds_ctx = odds_svc.get_odds_for_fixture(odds_stem)

    merged = merge_match_context_v2(facts=facts, openclaw_context=oc_ctx, odds_context=odds_ctx)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)
    run_id = persister.persist_scored_run(merged=merged, scored=scored)

    match_date = snapshot.match_meta.match_date_utc.date().isoformat()
    if save_match_results:
        db.save_match_result(
            match_date,
            snapshot.match_meta.home_team.name,
            snapshot.match_meta.away_team.name,
            home_score,
            away_score,
        )

    return {
        "run_id": run_id,
        "flashscore_stem": flashscore_stem,
        "match_date": match_date,
        "home_team": snapshot.match_meta.home_team.name,
        "away_team": snapshot.match_meta.away_team.name,
        "run_status": "scored",
    }


def run_v2_batch_persist_from_fixtures(
    fixtures_dir: str | Path,
    fixture_items: Sequence[Dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    save_match_results: bool = True,
    default_home_score: int = 1,
    default_away_score: int = 1,
) -> Dict[str, Any]:
    """
    Stage 1 batch bridge: fixture ingestion -> merge -> snapshot -> score -> run-level persist.

    Each item in ``fixture_items`` supports keys:
    - ``flashscore_stem`` (required)
    - ``openclaw_stem``, ``odds_stem`` (optional)
    - ``home_score``, ``away_score`` (optional per item; defaults apply)

    Writes to ``analysis_*_v2`` via ``SnapshotPersistenceServiceV2`` and optionally
    ``match_results`` via ``V2Database.save_match_result``.
    Does **not** write flat ``v2_predictions`` rows.
    """
    fixtures_path = Path(fixtures_dir)
    if not fixtures_path.exists():
        raise FileNotFoundError(f"Fixtures dir does not exist: {fixtures_path}")

    db = V2Database(db_path)
    persister = SnapshotPersistenceServiceV2(db_path=db_path)

    summary: Dict[str, Any] = {
        "pipeline": "run_level_batch_persist",
        "fixtures_dir": str(fixtures_path),
        "items_requested": len(fixture_items),
        "runs_persisted": 0,
        "match_results_saved": 0,
        "skipped": 0,
        "runs": [],
        "errors": [],
    }

    try:
        for item in fixture_items:
            stem = str(item.get("flashscore_stem") or "").strip()
            if not stem:
                summary["skipped"] += 1
                summary["errors"].append({"error": "missing flashscore_stem", "item": item})
                continue
            try:
                row = _persist_one_fixture_match(
                    fixtures_dir=fixtures_path,
                    flashscore_stem=stem,
                    openclaw_stem=item.get("openclaw_stem"),
                    odds_stem=item.get("odds_stem"),
                    persister=persister,
                    db=db,
                    save_match_results=save_match_results,
                    home_score=int(item.get("home_score", default_home_score)),
                    away_score=int(item.get("away_score", default_away_score)),
                )
                summary["runs_persisted"] += 1
                if save_match_results:
                    summary["match_results_saved"] += 1
                summary["runs"].append(row)
            except Exception as e:
                summary["skipped"] += 1
                summary["errors"].append({"flashscore_stem": stem, "error": str(e)})
                logger.exception("batch persist skip fixture %s: %s", stem, e)
    finally:
        persister.close()
        db.close()

    logger.info("v2 batch persist done: %s", summary)
    return summary


def run_v2_batch_persist_and_evaluate(
    fixtures_dir: str | Path,
    fixture_items: Sequence[Dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    save_match_results: bool = True,
    default_home_score: int = 1,
    default_away_score: int = 1,
) -> Dict[str, Any]:
    """
    Stage 1 end-to-end offline path:
    1) batch persist scored runs (fixtures)
    2) ``OfflineEvaluationServiceV2.evaluate(...)``
    """
    batch_summary = run_v2_batch_persist_from_fixtures(
        fixtures_dir,
        fixture_items,
        db_path=db_path,
        save_match_results=save_match_results,
        default_home_score=default_home_score,
        default_away_score=default_away_score,
    )

    eval_svc = OfflineEvaluationServiceV2(db_path=db_path)
    try:
        evaluation = eval_svc.evaluate(limit=max(1000, batch_summary.get("runs_persisted", 0) or 1))
    finally:
        eval_svc.close()

    return {
        "batch": batch_summary,
        "evaluation": evaluation,
    }
