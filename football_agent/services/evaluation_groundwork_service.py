"""Facade for Phase Evaluation A metrics over stored artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from football_agent.evaluation.groundwork_models import (
    EvaluationGroundworkRecord,
    build_evaluation_record,
)
from football_agent.evaluation.metrics import summarize_groundwork_records
from football_agent.odds.coverage import build_match_odds_coverage
from football_agent.odds.coverage_models import MatchOddsCoverage
from football_agent.odds.models import MatchOddsContext
from football_agent.services.odds_refresh_store import OddsRefreshStore


class EvaluationGroundworkService:
    """Compute coverage / calibration groundwork from refresh store or record lists."""

    def __init__(self, refresh_store: Optional[OddsRefreshStore] = None) -> None:
        self._refresh_store = refresh_store or OddsRefreshStore()

    def coverage_from_odds_contexts(
        self,
        contexts: List[MatchOddsContext],
    ) -> List[MatchOddsCoverage]:
        return [build_match_odds_coverage(ctx) for ctx in contexts]

    def records_from_refresh_store(self) -> List[EvaluationGroundworkRecord]:
        """Build minimal groundwork records from persisted odds refresh entries."""
        records: List[EvaluationGroundworkRecord] = []
        store_dir = self._refresh_store._root
        if not store_dir.exists():
            return records

        for path in store_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            current = payload.get("current") if isinstance(payload, dict) else None
            if not isinstance(current, dict):
                continue
            odds_raw = current.get("odds_context")
            if not isinstance(odds_raw, dict):
                continue
            try:
                ctx = MatchOddsContext.model_validate(odds_raw)
            except Exception:
                continue

            match_key = str(current.get("match_key") or path.stem)
            meta = ctx.meta
            rec = build_evaluation_record(
                match_key=match_key,
                home_team=meta.home_team,
                away_team=meta.away_team,
                odds_context=ctx,
                match_id=meta.match_id,
                match_date=(
                    meta.kickoff_utc.strftime("%Y-%m-%d") if meta.kickoff_utc else None
                ),
                competition_name=meta.competition_name,
            )
            records.append(rec)
        return records

    def summarize_refresh_store(self) -> Dict[str, Any]:
        records = self.records_from_refresh_store()
        return summarize_groundwork_records(records)
