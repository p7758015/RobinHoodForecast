"""Form block collector."""

from __future__ import annotations

from typing import Any, Dict, List

from football_agent.collectors.confidence import form_confidence
from football_agent.collectors.contracts import (
    BlockCollectionResult,
    MatchRef,
    SourceAttempt,
    utc_now,
)
from football_agent.flashscore.raw_enrich import form_has_signal


class FormCollector:
    """Collect recent results for form score derivation."""

    BLOCK = "form"
    SOURCE = "flashscore"

    def collect(self, raw: Dict[str, Any], ref: MatchRef) -> BlockCollectionResult:
        started = utc_now()
        warnings: List[str] = []

        form_raw = raw.get("form")
        if not isinstance(form_raw, dict):
            form_raw = {}

        if not form_has_signal(form_raw):
            finished = utc_now()
            return BlockCollectionResult(
                block=self.BLOCK,
                status="missing",
                confidence=0.0,
                source=self.SOURCE,
                collected_at_utc=finished,
                payload={},
                warnings=["form_no_signal"],
                attempts=[
                    SourceAttempt(
                        block=self.BLOCK,
                        source=self.SOURCE,
                        started_at_utc=started,
                        finished_at_utc=finished,
                        status="missing",
                        warnings=["form_no_signal"],
                        raw_ref=raw.get("_collector_raw_ref"),
                        duration_ms=int((finished - started).total_seconds() * 1000),
                    ),
                ],
                raw_ref=raw.get("_collector_raw_ref"),
            )

        home_block = form_raw.get("home") if isinstance(form_raw.get("home"), dict) else {}
        away_block = form_raw.get("away") if isinstance(form_raw.get("away"), dict) else {}

        payload: Dict[str, Any] = {
            "home": {
                "last_n_results": list(home_block.get("last_n_results") or []),
                "last_n_points": home_block.get("last_n_points"),
                "goals_for_last_n": home_block.get("goals_for_last_n"),
                "goals_against_last_n": home_block.get("goals_against_last_n"),
                "home_only_form": home_block.get("home_only_form"),
            },
            "away": {
                "last_n_results": list(away_block.get("last_n_results") or []),
                "last_n_points": away_block.get("last_n_points"),
                "goals_for_last_n": away_block.get("goals_for_last_n"),
                "goals_against_last_n": away_block.get("goals_against_last_n"),
                "away_only_form": away_block.get("away_only_form"),
            },
        }

        confidence, status, conf_warnings = form_confidence(payload)
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
