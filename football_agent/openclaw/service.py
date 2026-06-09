"""
Parallel ingestion entrypoint: OpenClaw → snapshot → scorer.

Does not replace LeagueAnalysisServiceV2; wire via feature flag later.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, MatchPredictionResultV2
from football_agent.openclaw.adapter import OpenClawSnapshotBuilder
from football_agent.openclaw.client import OpenClawClient, OpenClawConfigurationError
from football_agent.openclaw.models import OpenClawMatchPayload
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2

logger = logging.getLogger(__name__)


class OpenClawLeagueAnalysisService:
    """Build v2 snapshots from OpenClaw payloads and run the existing scorer."""

    def __init__(
        self,
        client: Optional[OpenClawClient] = None,
        snapshot_builder: Optional[OpenClawSnapshotBuilder] = None,
        scorer: Optional[LeagueScorerV2] = None,
        *,
        fetch_matches_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    ) -> None:
        self._client = client if client is not None else OpenClawClient()
        self._builder = snapshot_builder if snapshot_builder is not None else OpenClawSnapshotBuilder()
        self._scorer = scorer if scorer is not None else LeagueScorerV2()
        self._fetch_matches_raw = fetch_matches_fn

    def build_snapshot(self, payload: OpenClawMatchPayload) -> MatchAnalysisSnapshotV2:
        return self._builder.build(payload)

    def analyze_from_payload(self, payload: OpenClawMatchPayload) -> MatchPredictionResultV2:
        snap = self.build_snapshot(payload)
        return self._scorer.score_snapshot(snap)

    def analyze_from_dict(self, data: Dict[str, Any]) -> MatchPredictionResultV2:
        return self.analyze_from_payload(self._client.fetch_match_payload_from_dict(data))

    def analyze_match_by_teams(
        self,
        home_team_name: str,
        away_team_name: str,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> Tuple[Optional[MatchPredictionResultV2], Optional[str]]:
        """Resolve via same-date batch + fuzzy team names (reuses TEAM_ALIASES)."""

        from football_agent.openclaw.match_resolve import resolve_prediction_result_by_teams

        results = self.analyze_matches_for_date(date_str, competition_code)
        pred, err = resolve_prediction_result_by_teams(home_team_name, away_team_name, results)
        if err:
            logger.warning("OpenClaw match resolve: %s", err)
            return None, err
        return pred, None

    def analyze_match(self, *, openclaw_event_id: str) -> MatchPredictionResultV2:
        """Fetch by OpenClaw id (requires OPENCLAW_BASE_URL)."""

        payload = self._client.fetch_match_payload(openclaw_event_id)
        return self.analyze_from_payload(payload)

    def analyze_matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[MatchPredictionResultV2]:
        if self._fetch_matches_raw:
            payloads = []
            for blob in self._fetch_matches_raw(date_str, competition_code):
                try:
                    payloads.append(self._client.fetch_match_payload_from_dict(blob))
                except Exception as e:
                    logger.warning("OpenClaw date batch: skipped blob: %s", e)
        else:
            try:
                payloads = self._client.fetch_matches_payloads_for_date(date_str, competition_code)
            except OpenClawConfigurationError:
                logger.warning("OpenClaw not configured — returning empty list for %s.", date_str)
                return []

        out: List[MatchPredictionResultV2] = []
        for pl in payloads:
            try:
                out.append(self.analyze_from_payload(pl))
            except Exception as e:
                logger.exception("OpenClaw analyze failed for payload: %s", e)

        out.sort(key=lambda r: r.match_meta.match_date_utc)
        return out
