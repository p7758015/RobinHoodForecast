"""
Flashscore HTTP → merge → snapshot → score → optional persistence.

Runtime-safe subset used by Telegram and other long-lived entrypoints.
OpenClaw context and live odds are optional with graceful fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from football_agent import config
from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.merge.news_merge import merge_news_into_merged_context
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.collectors.contracts import MatchCollectionBundle
from football_agent.collectors.odds_bridge import (
    OddsBridgeSource,
    refresh_odds_source_status,
    resolve_pipeline_odds_context,
)
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.services.flashscore_facts_resolver import pick_facts_by_teams
from football_agent.output.match_context_display import extract_openclaw_highlights
from football_agent.services.enrichment_contract import (
    SOURCE_FAILED,
    SOURCE_PARTIAL,
    SOURCE_SKIPPED,
    SOURCE_SKIPPED_NOT_CONFIGURED,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult, fetch_enrichment_for_facts
from football_agent.services.persistence_service_v2 import SnapshotPersistenceServiceV2
from football_agent.services.competition_classifier import (
    CompetitionClassification,
    classify_competition_from_facts,
    refine_meta_tournament_type,
)
from football_agent.services.competition_guardrails import (
    CompetitionGuardrailResult,
    apply_competition_guardrails,
)
from football_agent.services.scoring_service_v2 import ScoredRunV2, ScoringServiceV2
from football_agent.scorers.routing import ScorerRoutingDecision
from football_agent.services.source_completeness import (
    SourceCompletenessReport,
    build_completeness_report,
)
from football_agent.storage.match_key import build_match_key_from_merged

logger = logging.getLogger(__name__)


@dataclass
class LivePipelineResult:
    success: bool
    path: str
    scored_run: Optional[ScoredRunV2] = None
    run_id: Optional[str] = None
    match_key: Optional[str] = None
    persisted: bool = False
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    context_highlights: List[str] = field(default_factory=list)
    openclaw_link_strategy: Optional[str] = None
    odds_link_strategy: Optional[str] = None
    stage_failed: Optional[str] = None
    user_message: Optional[str] = None
    completeness: Optional[SourceCompletenessReport] = None
    enrichment_mode: Optional[str] = None
    odds_source: Optional[str] = None
    enrichment_backend: Optional[str] = None
    competition_classification: Optional[CompetitionClassification] = None
    competition_guardrail: Optional[CompetitionGuardrailResult] = None
    routing_decision: Optional[ScorerRoutingDecision] = None


def _resolve_scraper_url(cli_url: Optional[str] = None) -> Optional[str]:
    return (cli_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/") or None


class LiveFlashscorePipeline:
    """Single-match v2 pipeline from live Flashscore scraper."""

    def __init__(
        self,
        *,
        scraper_url: Optional[str] = None,
        scraper_api_key: Optional[str] = None,
        openclaw_url: Optional[str] = None,
        openclaw_api_key: Optional[str] = None,
        skip_openclaw: bool = False,
        odds_url: Optional[str] = None,
        odds_api_key: Optional[str] = None,
        skip_odds: bool = False,
        fixtures_dir: str | Path | None = None,
        odds_fixture_stem: Optional[str] = None,
        db_path: str | Path | None = None,
        persist: bool = True,
    ) -> None:
        self._scraper_url = scraper_url
        self._scraper_api_key = scraper_api_key
        self._openclaw_url = openclaw_url
        self._openclaw_api_key = openclaw_api_key
        self._skip_openclaw = skip_openclaw
        self._odds_url = odds_url
        self._odds_api_key = odds_api_key
        self._skip_odds = skip_odds
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self._odds_fixture_stem = (odds_fixture_stem or "").strip() or None
        self._db_path = db_path
        self._persist = persist

    def analyze_flashscore_url(
        self,
        match_url: str,
        *,
        discovery_hints: Optional[dict] = None,
    ) -> LivePipelineResult:
        return self._run(
            path="flashscore_url",
            match_url=match_url.strip(),
            discovery_hints=discovery_hints,
        )

    def analyze_teams(
        self,
        home_team: str,
        away_team: str,
        date_str: str,
        *,
        competition_code: Optional[str] = None,
    ) -> LivePipelineResult:
        return self._run(
            path="team_query",
            home=home_team.strip(),
            away=away_team.strip(),
            date_str=date_str.strip(),
            competition_code=competition_code,
        )

    def _load_odds_fixture(
        self,
    ) -> tuple[Optional[Any], Dict[str, str], List[str]]:
        from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
        from football_agent.odds.models import MatchOddsContext
        from football_agent.odds.service import OddsIngestionService

        warnings: List[str] = []
        if not self._fixtures_dir:
            return None, {"odds": "failed"}, ["odds_fixture_requires_fixtures_dir"]
        ctx: Optional[MatchOddsContext] = OddsIngestionService(
            FixtureFileOddsAdapter(self._fixtures_dir),
        ).get_odds_for_fixture(self._odds_fixture_stem or "")
        if ctx is None:
            return None, {"odds": "failed"}, [f"odds_fixture_not_found:{self._odds_fixture_stem}"]
        return ctx, {"odds": "fixture"}, warnings

    def _fetch_facts_collector(
        self,
        scraper_url: str,
        *,
        match_url: Optional[str],
        home: Optional[str],
        away: Optional[str],
        date_str: Optional[str],
        competition_code: Optional[str],
        discovery_hints: Optional[dict] = None,
    ) -> tuple[
        Optional[FlashscoreMatchFacts],
        Dict[str, str],
        Optional[str],
        List[str],
        Optional[MatchCollectionBundle],
    ]:
        from football_agent.services.match_collection_service import MatchCollectionService

        svc = MatchCollectionService(
            scraper_url,
            api_key=self._scraper_api_key or config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        if match_url:
            result = svc.collect_for_url(match_url, discovery_hints=discovery_hints)
        elif home and away and date_str:
            result = svc.collect_for_teams(home, away, date_str, competition_code=competition_code)
        else:
            return None, {"flashscore": "failed", "collector": "skipped"}, "Нужны команды и дата.", [], None

        sources: Dict[str, str] = {"collector": "ok" if result.success else "failed"}
        if result.aborted:
            sources["flashscore"] = "failed"
            sources["collector"] = "aborted"
            return None, sources, result.error_message, list(result.warnings or []), result.bundle

        if not result.success or not result.facts:
            sources["flashscore"] = "failed"
            return None, sources, result.error_message, list(result.warnings or []), result.bundle

        sources["flashscore"] = "ok"
        if result.bundle:
            sources["collector_status"] = result.bundle.overall_status
        return result.facts, sources, None, list(result.warnings or []), result.bundle

    def _fetch_facts(
        self,
        scraper_url: str,
        *,
        match_url: Optional[str],
        home: Optional[str],
        away: Optional[str],
        date_str: Optional[str],
        competition_code: Optional[str],
    ) -> tuple[Optional[FlashscoreMatchFacts], Dict[str, str], Optional[str], Optional[dict]]:
        adapter = HttpFlashscoreScraperAdapter(
            scraper_url,
            api_key=self._scraper_api_key or config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        service = FlashscoreIngestionService(adapter)

        if match_url:
            try:
                raw = adapter.fetch_match_raw(match_url)
            except (FlashscoreScraperUnavailableError, FlashscoreScraperError) as exc:
                return None, {"flashscore": "failed"}, str(exc), None
            if not raw:
                return None, {"flashscore": "failed"}, "Пустой ответ scraper.", None
            facts = service._map_raw_to_facts(raw)  # type: ignore[attr-defined]
            return facts, {"flashscore": "ok"}, None, raw

        if not (home and away and date_str):
            return None, {"flashscore": "failed"}, "Нужны команды и дата.", None

        try:
            raw_list = adapter.fetch_matches_for_date(date_str, competition_code)
            facts_list = [service._map_raw_to_facts(raw) for raw in raw_list]  # type: ignore[attr-defined]
        except (FlashscoreScraperUnavailableError, FlashscoreScraperError) as exc:
            return None, {"flashscore": "failed"}, str(exc), None

        facts, err = pick_facts_by_teams(facts_list, home, away)
        if err or not facts:
            return None, {"flashscore": "failed"}, err or "Матч не найден.", None

        matched_raw: Optional[dict] = None
        for raw in raw_list:
            if str(raw.get("match_id") or "") == facts.meta.match_id:
                matched_raw = raw
                break
        if matched_raw is None:
            for raw in raw_list:
                if (
                    str(raw.get("home_team_name") or raw.get("home") or "") == facts.meta.home_team_name
                    and str(raw.get("away_team_name") or raw.get("away") or "") == facts.meta.away_team_name
                ):
                    matched_raw = raw
                    break
        return facts, {"flashscore": "ok"}, None, matched_raw

    def _run(
        self,
        *,
        path: str,
        match_url: Optional[str] = None,
        home: Optional[str] = None,
        away: Optional[str] = None,
        date_str: Optional[str] = None,
        competition_code: Optional[str] = None,
        discovery_hints: Optional[dict] = None,
    ) -> LivePipelineResult:
        scraper_url = _resolve_scraper_url(self._scraper_url)
        if not scraper_url:
            return LivePipelineResult(
                success=False,
                path=path,
                stage_failed="config",
                user_message=(
                    "Flashscore scraper не настроен. "
                    "Укажите FLASHSCORE_SCRAPER_URL в .env."
                ),
                sources={"flashscore": "not_configured"},
            )

        collector_warnings: List[str] = []
        collector_bundle: Optional[MatchCollectionBundle] = None
        flashscore_raw: Optional[dict] = None
        try:
            if config.USE_COLLECTOR_LAYER:
                facts, sources, fetch_err, collector_warnings, collector_bundle = self._fetch_facts_collector(
                    scraper_url,
                    match_url=match_url,
                    home=home,
                    away=away,
                    date_str=date_str,
                    competition_code=competition_code,
                    discovery_hints=discovery_hints,
                )
            else:
                fetch_out = self._fetch_facts(
                    scraper_url,
                    match_url=match_url,
                    home=home,
                    away=away,
                    date_str=date_str,
                    competition_code=competition_code,
                )
                if len(fetch_out) >= 4:
                    facts, sources, fetch_err, flashscore_raw = fetch_out
                else:
                    facts, sources, fetch_err = fetch_out  # type: ignore[misc]
                    flashscore_raw = None
        except FlashscoreScraperConfigurationError as exc:
            logger.error("Flashscore config error: %s", exc)
            return LivePipelineResult(
                success=False,
                path=path,
                stage_failed="config",
                user_message="Flashscore scraper не настроен.",
                sources={"flashscore": "config_error"},
            )

        if not facts:
            logger.warning(
                "Flashscore ingest failed path=%s home=%s away=%s date=%s err=%s",
                path,
                home,
                away,
                date_str,
                fetch_err,
            )
            return LivePipelineResult(
                success=False,
                path=path,
                stage_failed="flashscore_ingest",
                user_message=_user_message_for_fetch_failure(path, fetch_err),
                sources=sources,
            )

        skip_live_odds = self._skip_odds or bool(self._odds_fixture_stem)
        enrichment: EnrichmentFetchResult = fetch_enrichment_for_facts(
            facts,
            openclaw_url=self._openclaw_url,
            openclaw_api_key=self._openclaw_api_key,
            skip_openclaw=self._skip_openclaw,
            odds_url=self._odds_url,
            odds_api_key=self._odds_api_key,
            skip_odds=skip_live_odds,
            home_override=home,
            away_override=away,
            date_override=date_str,
            competition_override=competition_code,
            match_url_override=match_url,
        )
        oc_ctx = enrichment.context
        enrichment_odds = enrichment.odds
        sources.update(enrichment.sources)
        warnings: List[str] = list(enrichment.warnings)
        if collector_warnings:
            warnings.extend(collector_warnings)

        fixture_odds = None
        if self._odds_fixture_stem:
            fixture_odds, fixture_src, fixture_warn = self._load_odds_fixture()
            sources.update(fixture_src)
            warnings.extend(fixture_warn)

        from football_agent.collectors.odds_bridge import build_odds_bundle_from_flashscore_raw

        embedded_odds_bundle: Optional[MatchCollectionBundle] = None
        if (
            not config.USE_COLLECTOR_LAYER
            and config.USE_EMBEDDED_FLASHSCORE_ODDS
            and flashscore_raw
        ):
            embedded_odds_bundle = build_odds_bundle_from_flashscore_raw(
                flashscore_raw,
                match_key=facts.meta.match_id or "flashscore",
            )
            if embedded_odds_bundle is not None:
                warnings.append("odds_embedded_flashscore_raw")

        effective_collector_bundle = collector_bundle or embedded_odds_bundle

        odds_bridge_source: OddsBridgeSource = "none"
        odds_ctx, bridge_warnings, odds_bridge_source = resolve_pipeline_odds_context(
            facts=facts,
            collector_bundle=effective_collector_bundle,
            enrichment_odds=None if skip_live_odds else enrichment_odds,
            fixture_odds=fixture_odds,
        )
        warnings.extend(bridge_warnings)
        if odds_bridge_source != "none":
            sources["odds_bridge"] = odds_bridge_source
        refresh_odds_source_status(sources, odds_ctx)
        context_highlights = extract_openclaw_highlights(oc_ctx)
        enrichment_mode = enrichment.enrichment_mode
        odds_source = enrichment.odds_source
        enrichment_backend = enrichment.sources.get("enrichment_backend")

        competition_clf = classify_competition_from_facts(facts)
        facts = facts.model_copy(update={"meta": refine_meta_tournament_type(facts.meta)})

        try:
            merged = merge_match_context_v2(
                facts=facts,
                openclaw_context=oc_ctx,
                odds_context=odds_ctx,
            )
            merged = merge_news_into_merged_context(merged, enrichment.news)
            snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
            scored = ScoringServiceV2().score_snapshot_with_report(
                snapshot,
                report,
                classification=competition_clf,
            )
            routing_decision = scored.routing_decision
            if routing_decision and routing_decision.route == "league_full":
                scored, competition_guardrail = apply_competition_guardrails(scored, competition_clf)
            else:
                from football_agent.services.competition_guardrails import CompetitionGuardrailResult

                competition_guardrail = CompetitionGuardrailResult(
                    classification=competition_clf,
                    guardrail_applied=False,
                    confidence_penalty=0.0,
                    original_confidence=scored.prediction.overall_confidence_score,
                    adjusted_confidence=scored.prediction.overall_confidence_score,
                )
        except Exception as exc:
            logger.exception("Pipeline failed after flashscore ingest path=%s", path)
            return LivePipelineResult(
                success=False,
                path=path,
                stage_failed="merge_score",
                user_message="Не удалось построить анализ матча. Попробуйте позже.",
                sources=sources,
                warnings=[str(exc)],
            )

        openclaw_link = report.openclaw_link_strategy
        odds_link = report.odds_link_strategy
        oc_status = sources.get("openclaw")
        if oc_status == SOURCE_FAILED:
            warnings.append("openclaw_context_unavailable")
        elif oc_status == SOURCE_SKIPPED:
            warnings.append("openclaw_context_skipped")
        elif oc_status == SOURCE_SKIPPED_NOT_CONFIGURED:
            if sources.get("brave_news") not in ("ok", "partial"):
                warnings.append("openclaw_enrichment_not_configured")
        elif oc_status == SOURCE_PARTIAL:
            warnings.append("openclaw_context_partial")
        if openclaw_link in ("provided_without_link", "unlinked") and oc_ctx is not None:
            warnings.append("openclaw_context_link_mismatch")

        odds_status = sources.get("odds")
        if odds_status == SOURCE_FAILED:
            warnings.append("odds_unavailable")
        elif odds_status == SOURCE_SKIPPED:
            warnings.append("odds_skipped")
        elif odds_status == SOURCE_SKIPPED_NOT_CONFIGURED:
            warnings.append("odds_not_configured")
        elif odds_status == SOURCE_PARTIAL:
            warnings.append("odds_partial")
        if odds_link in ("provided_without_link", "unlinked") and odds_ctx is not None:
            warnings.append("odds_link_mismatch")

        if report.merge_missing_blocks:
            warnings.append(f"incomplete_data: {', '.join(report.merge_missing_blocks[:3])}")
        if report.merge_warnings:
            warnings.extend(report.merge_warnings[:2])
        if scored.scoring_warnings:
            warnings.extend(scored.scoring_warnings[:2])
        if routing_decision is not None:
            warnings.append(f"scorer_route:{routing_decision.route}")
        if competition_guardrail.guardrail_applied:
            warnings.extend(competition_guardrail.warnings)

        completeness = build_completeness_report(
            facts=facts,
            sources=sources,
            warnings=warnings,
            openclaw_ctx=oc_ctx,
            odds_ctx=odds_ctx,
            openclaw_link=openclaw_link,
            odds_link=odds_link,
            enrichment_mode=enrichment_mode,
            odds_source=odds_source,
            enrichment_backend=enrichment_backend,
            competition_context=competition_clf.category.value,
            competition_guardrail_applied=competition_guardrail.guardrail_applied,
        )
        if facts.provenance.missing_blocks:
            warnings.append(
                f"flashscore_missing:{','.join(facts.provenance.missing_blocks[:4])}",
            )
        logger.info(
            "Pipeline completeness path=%s coverage=%.2f fs_missing=%s competition=%s guardrail=%s sources=%s",
            path,
            completeness.coverage_score(),
            completeness.flashscore_missing,
            competition_clf.category.value,
            competition_guardrail.guardrail_applied,
            sources,
        )

        run_id: Optional[str] = None
        match_key: Optional[str] = None
        persisted = False
        if self._persist:
            try:
                pers = SnapshotPersistenceServiceV2(db_path=self._db_path)
                try:
                    run_id = pers.persist_scored_run(merged=merged, scored=scored)
                    match_key = build_match_key_from_merged(merged)
                    persisted = True
                    logger.info(
                        "Persisted run path=%s run_id=%s match_key=%s",
                        path,
                        run_id,
                        match_key,
                    )
                finally:
                    pers.close()
            except Exception as exc:
                logger.exception("Persistence failed path=%s", path)
                warnings.append(f"persist_failed: {exc}")

        return LivePipelineResult(
            success=True,
            path=path,
            scored_run=scored,
            run_id=run_id,
            match_key=match_key,
            persisted=persisted,
            sources=sources,
            warnings=warnings,
            context_highlights=context_highlights,
            openclaw_link_strategy=openclaw_link,
            odds_link_strategy=odds_link,
            completeness=completeness,
            enrichment_mode=enrichment_mode,
            odds_source=odds_source,
            enrichment_backend=enrichment_backend,
            competition_classification=competition_clf,
            competition_guardrail=competition_guardrail,
            routing_decision=routing_decision,
        )


def _user_message_for_fetch_failure(path: str, err: Optional[str]) -> str:
    if path == "team_query":
        return (
            "Не удалось найти матч по командам на указанную дату.\n\n"
            "Сейчас надёжнее всего работает ссылка Flashscore на матч.\n"
            "Пример: https://www.flashscore.com/match/football/.../?mid=...\n\n"
            f"Детали: {err or 'матч не найден'}"
        )
    return (
        "Не удалось получить данные матча из Flashscore.\n"
        "Проверьте ссылку и что scraper запущен.\n\n"
        f"Детали: {err or 'ошибка scraper'}"
    )
