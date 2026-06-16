"""Standings / teams base inputs block collector."""

from __future__ import annotations

from typing import Any, Dict, List

from football_agent.collectors.confidence import standings_confidence
from football_agent.collectors.contracts import (
    BlockCollectionResult,
    MatchRef,
    SourceAttempt,
    utc_now,
)
from football_agent.flashscore.raw_enrich import standings_has_signal


class StandingsCollector:
    """Collect league table inputs for teams block foundation."""

    BLOCK = "teams"
    SOURCE = "flashscore"

    def collect(self, raw: Dict[str, Any], ref: MatchRef) -> BlockCollectionResult:
        started = utc_now()
        warnings: List[str] = []

        standings_raw = raw.get("standings")
        if not isinstance(standings_raw, dict):
            standings_raw = {}

        if not standings_has_signal(standings_raw):
            finished = utc_now()
            return BlockCollectionResult(
                block=self.BLOCK,
                status="missing",
                confidence=0.0,
                source=self.SOURCE,
                collected_at_utc=finished,
                payload={},
                warnings=["standings_no_signal"],
                attempts=[
                    SourceAttempt(
                        block=self.BLOCK,
                        source=self.SOURCE,
                        started_at_utc=started,
                        finished_at_utc=finished,
                        status="missing",
                        warnings=["standings_no_signal"],
                        raw_ref=raw.get("_collector_raw_ref"),
                        duration_ms=int((finished - started).total_seconds() * 1000),
                    ),
                ],
                raw_ref=raw.get("_collector_raw_ref"),
            )

        payload: Dict[str, Any] = {
            "home_position": standings_raw.get("home_position"),
            "away_position": standings_raw.get("away_position"),
            "home_points": standings_raw.get("home_points"),
            "away_points": standings_raw.get("away_points"),
            "home_goal_difference": standings_raw.get("home_goal_difference"),
            "away_goal_difference": standings_raw.get("away_goal_difference"),
            "home_matches_played": standings_raw.get("home_matches_played"),
            "away_matches_played": standings_raw.get("away_matches_played"),
        }

        confidence, status, conf_warnings = standings_confidence(payload)
        warnings.extend(conf_warnings)

        finished = utc_now()
        return BlockCollectionResult(
            block=self.BLOCK,
            status=status,
            confidence=confidence,
            source=self.SOURCE,
            collected_at_utc=finished,
            payload=payload,
            warnings=warnings,
            attempts=[
                SourceAttempt(
                    block=self.BLOCK,
                    source=self.SOURCE,
                    started_at_utc=started,
                    finished_at_utc=finished,
                    status=status,  # type: ignore[arg-type]
                    warnings=list(warnings),
                    raw_ref=raw.get("_collector_raw_ref"),
                    duration_ms=int((finished - started).total_seconds() * 1000),
                ),
            ],
            raw_ref=raw.get("_collector_raw_ref"),
        )
