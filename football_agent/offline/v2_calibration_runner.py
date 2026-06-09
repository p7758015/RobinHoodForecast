"""
Offline batch: finished matches -> v2 snapshots -> scorer -> v2_predictions table.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from football_agent.config import API_FOOTBALL_KEY, FOOTBALL_DATA_API_KEY
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.models import Match
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
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
) -> dict:
    """
    Score finished matches for a date and store all v2 market rows.
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
            )
        )
        current += timedelta(days=1)
    return summaries
