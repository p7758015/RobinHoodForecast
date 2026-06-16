"""
Match collection service — Flashscore-first collector entry (Phase A/B.1).

Used when USE_COLLECTOR_LAYER=true. Does not replace enrichment (OpenClaw/odds).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from football_agent import config
from football_agent.collectors.apply_bundle import apply_bundle_to_facts
from football_agent.collectors.contracts import CollectionTrace, MatchCollectionBundle, MatchRef
from football_agent.collectors.flashscore.client import FlashscoreCollectorClient
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.adapters.errors import FlashscoreScraperError
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.services.flashscore_facts_resolver import pick_facts_by_teams

logger = logging.getLogger(__name__)


@dataclass
class MatchCollectionServiceResult:
    success: bool
    facts: Optional[FlashscoreMatchFacts] = None
    bundle: Optional[MatchCollectionBundle] = None
    trace: Optional[CollectionTrace] = None
    warnings: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    aborted: bool = False


class MatchCollectionService:
    def __init__(
        self,
        scraper_url: str,
        *,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        self._client = FlashscoreCollectorClient(
            scraper_url,
            api_key=api_key,
            timeout_s=timeout_s,
        )
        self._orchestrator = MatchCollectorOrchestrator()

    def collect_for_url(self, match_url: str) -> MatchCollectionServiceResult:
        ref = MatchRef(match_url=match_url.strip())
        try:
            raw = self._client.fetch_match_raw_enriched(match_url)
        except FlashscoreScraperError as exc:
            return MatchCollectionServiceResult(
                success=False,
                error_message=str(exc),
            )

        return self._finish_from_raw(raw, ref)

    def collect_for_teams(
        self,
        home: str,
        away: str,
        date_str: str,
        *,
        competition_code: Optional[str] = None,
    ) -> MatchCollectionServiceResult:
        ref = MatchRef(
            home_team=home.strip(),
            away_team=away.strip(),
            date=date_str.strip(),
            competition_code=competition_code,
        )
        try:
            raw_list = self._client.fetch_matches_for_date_raw(date_str, competition_code)
        except FlashscoreScraperError as exc:
            return MatchCollectionServiceResult(
                success=False,
                error_message=str(exc),
            )

        facts_candidates = [self._client.map_to_facts(r) for r in raw_list]
        facts, err = pick_facts_by_teams(facts_candidates, home, away)
        if err or not facts:
            return MatchCollectionServiceResult(success=False, error_message=err or "match not found")

        matched_raw = next(
            (
                r
                for r in raw_list
                if str(r.get("match_id") or "") == facts.meta.match_id
                or (
                    str(r.get("home_team_name") or r.get("home")) == facts.meta.home_team_name
                    and str(r.get("away_team_name") or r.get("away")) == facts.meta.away_team_name
                )
            ),
            raw_list[0] if raw_list else {},
        )
        return self._finish_from_raw(matched_raw, ref)

    def _finish_from_raw(self, raw: dict, ref: MatchRef) -> MatchCollectionServiceResult:
        match_key = self._client._derive_match_key(raw)  # noqa: SLF001
        bundle, trace = self._orchestrator.collect_from_raw(raw, ref, match_key=match_key)

        if bundle.aborted:
            reason = bundle.abort_reason or "match_meta_failed"
            return MatchCollectionServiceResult(
                success=False,
                bundle=bundle,
                trace=trace,
                warnings=[f"collector_aborted:{reason}"],
                error_message="Flashscore match metadata invalid (teams or competition).",
                aborted=True,
            )

        facts = self._client.map_to_facts(raw)
        facts, apply_warnings = apply_bundle_to_facts(facts, bundle)

        parse_report = raw.get("_fixture_parse_report")
        if isinstance(parse_report, dict):
            self._client.save_parse_report(match_key, "match_meta", parse_report)

        warnings = list(apply_warnings)
        warnings.append(f"collector_trace_id:{trace.trace_id}")

        bundle = bundle.model_copy(update={"flashscore_facts": facts})

        return MatchCollectionServiceResult(
            success=True,
            facts=facts,
            bundle=bundle,
            trace=trace,
            warnings=warnings,
        )


def collect_match_facts(
    *,
    match_url: Optional[str] = None,
    home: Optional[str] = None,
    away: Optional[str] = None,
    date_str: Optional[str] = None,
    competition_code: Optional[str] = None,
    scraper_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> MatchCollectionServiceResult:
    """Convenience wrapper using config defaults."""
    base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
    if not base:
        return MatchCollectionServiceResult(
            success=False,
            error_message="FLASHSCORE_SCRAPER_URL not configured",
        )
    svc = MatchCollectionService(
        base,
        api_key=api_key or config.FLASHSCORE_SCRAPER_API_KEY,
        timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
    )
    if match_url:
        return svc.collect_for_url(match_url)
    if home and away and date_str:
        return svc.collect_for_teams(home, away, date_str, competition_code=competition_code)
    return MatchCollectionServiceResult(
        success=False,
        error_message="match_url or home+away+date required",
    )
