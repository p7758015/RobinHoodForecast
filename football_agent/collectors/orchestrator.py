"""Match collector orchestrator (Phase A/B.1 skeleton)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from football_agent.collectors.confidence import bundle_overall_confidence
from football_agent.collectors.contracts import (
    BlockCollectionResult,
    BundleStatus,
    CollectionTrace,
    MatchCollectionBundle,
    MatchRef,
)
from football_agent.collectors.flashscore.fixture_collector import FixtureMatchCollector
from football_agent.collectors.flashscore.form_collector import FormCollector
from football_agent.collectors.flashscore.odds_collector import FlashscoreOddsCollector
from football_agent.collectors.flashscore.standings_collector import StandingsCollector
from football_agent.collectors.trace import CollectionTraceBuilder
from football_agent.registries.source_registry import DEFAULT_SOURCE_REGISTRY, SourceRegistry


class MatchCollectorOrchestrator:
    """
    Runs block collectors in order: match_meta → teams (standings) → form → odds.

    Aborts only when match_meta is failed (invalid teams/competition).
    """

    def __init__(self, registry: Optional[SourceRegistry] = None) -> None:
        self._registry = registry or DEFAULT_SOURCE_REGISTRY
        self._fixture = FixtureMatchCollector()
        self._standings = StandingsCollector()
        self._form = FormCollector()
        self._odds = FlashscoreOddsCollector()

    def collect_from_raw(
        self,
        raw: dict,
        ref: MatchRef,
        *,
        match_key: Optional[str] = None,
    ) -> Tuple[MatchCollectionBundle, CollectionTrace]:
        key = match_key or _match_key_from_raw(raw, ref)
        trace_builder = CollectionTraceBuilder(key)

        blocks: Dict[str, BlockCollectionResult] = {}

        meta_result = self._fixture.collect(raw, ref)
        blocks[meta_result.block] = meta_result
        trace_builder.record_block(meta_result)

        if meta_result.status == "failed":
            trace_builder.add_warning(
                "collector_aborted",
                "match_meta",
                "match_meta validation failed",
                severity="error",
            )
            bundle = MatchCollectionBundle(
                match_key=key,
                match_ref=ref,
                blocks=blocks,
                overall_confidence=0.0,
                overall_status="aborted",
                trace_id=trace_builder.trace_id,
                aborted=True,
                abort_reason="match_meta_failed",
            )
            return bundle, trace_builder.finish()

        standings_result = self._standings.collect(raw, ref)
        blocks[standings_result.block] = standings_result
        trace_builder.record_block(standings_result)

        form_result = self._form.collect(raw, ref)
        blocks[form_result.block] = form_result
        trace_builder.record_block(form_result)

        odds_result = self._odds.collect(raw, ref)
        blocks[odds_result.block] = odds_result
        trace_builder.record_block(odds_result)

        confidences = {k: v.confidence for k, v in blocks.items()}
        overall_conf = bundle_overall_confidence(confidences)
        overall_status = _derive_bundle_status(blocks, aborted=False)

        bundle = MatchCollectionBundle(
            match_key=key,
            match_ref=ref,
            blocks=blocks,
            overall_confidence=overall_conf,
            overall_status=overall_status,
            trace_id=trace_builder.trace_id,
            aborted=False,
        )
        return bundle, trace_builder.finish()


def _derive_bundle_status(blocks: Dict[str, BlockCollectionResult], *, aborted: bool) -> BundleStatus:
    if aborted:
        return "aborted"
    statuses = [b.status for b in blocks.values()]
    if all(s == "ok" for s in statuses):
        return "ok"
    meta = blocks.get("match_meta")
    if meta and meta.status == "failed":
        return "failed"
    return "partial"


def _match_key_from_raw(raw: dict, ref: MatchRef) -> str:
    mid = str(raw.get("match_id") or raw.get("id") or ref.match_id or "unknown")
    home = str(raw.get("home_team_name") or raw.get("home") or ref.home_team or "home")
    away = str(raw.get("away_team_name") or raw.get("away") or ref.away_team or "away")
    return f"{mid}:{home}:{away}"
