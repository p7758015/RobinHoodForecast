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
from football_agent.domain.models import Team
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.normalizers.team_name_resolver import score_team_query
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


def _resolve_scraper_url(cli_url: Optional[str] = None) -> Optional[str]:
    return (cli_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/") or None


def _pick_facts_by_teams(
    facts_list: List[FlashscoreMatchFacts],
    home_query: str,
    away_query: str,
    *,
    min_score: float = 0.72,
) -> tuple[Optional[FlashscoreMatchFacts], Optional[str]]:
    if not facts_list:
        return None, "На указанную дату матчей не найдено."

    scored: List[tuple[FlashscoreMatchFacts, float]] = []
    for facts in facts_list:
        home_team = Team(id=0, name=facts.meta.home_team_name, short_name=facts.meta.home_team_name)
        away_team = Team(id=0, name=facts.meta.away_team_name, short_name=facts.meta.away_team_name)
        sh = score_team_query(home_query, home_team)
        sa = score_team_query(away_query, away_team)
        combined = (sh + sa) / 2.0
        if sh >= 0.5 and sa >= 0.5:
            scored.append((facts, combined))

    if not scored:
        return None, f"Матч не найден: {home_query} — {away_query}."

    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    if best_score < min_score:
        return None, (
            f"Матч не найден уверенно: {home_query} — {away_query} "
            f"(score {best_score:.2f})."
        )
    return best, None


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
        self._db_path = db_path
        self._persist = persist

    def analyze_flashscore_url(self, match_url: str) -> LivePipelineResult:
        return self._run(
            path="flashscore_url",
            match_url=match_url.strip(),
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

    def _fetch_facts(
        self,
        scraper_url: str,
        *,
        match_url: Optional[str],
        home: Optional[str],
        away: Optional[str],
        date_str: Optional[str],
        competition_code: Optional[str],
    ) -> tuple[Optional[FlashscoreMatchFacts], Dict[str, str], Optional[str]]:
        adapter = HttpFlashscoreScraperAdapter(
            scraper_url,
            api_key=self._scraper_api_key or config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        service = FlashscoreIngestionService(adapter)

        if match_url:
            try:
                facts = service.get_facts_for_match(match_url)
            except (FlashscoreScraperUnavailableError, FlashscoreScraperError) as exc:
                return None, {"flashscore": "failed"}, str(exc)
            if not facts:
                return None, {"flashscore": "failed"}, "Пустой ответ scraper."
            return facts, {"flashscore": "ok"}, None

        if not (home and away and date_str):
            return None, {"flashscore": "failed"}, "Нужны команды и дата."

        try:
            raw_list = adapter.fetch_matches_for_date(date_str, competition_code)
            facts_list = [service._map_raw_to_facts(raw) for raw in raw_list]  # type: ignore[attr-defined]
        except (FlashscoreScraperUnavailableError, FlashscoreScraperError) as exc:
            return None, {"flashscore": "failed"}, str(exc)

        facts, err = _pick_facts_by_teams(facts_list, home, away)
        if err or not facts:
            return None, {"flashscore": "failed"}, err or "Матч не найден."
        return facts, {"flashscore": "ok"}, None

    def _run(
        self,
        *,
        path: str,
        match_url: Optional[str] = None,
        home: Optional[str] = None,
        away: Optional[str] = None,
        date_str: Optional[str] = None,
        competition_code: Optional[str] = None,
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

        try:
            facts, sources, fetch_err = self._fetch_facts(
                scraper_url,
                match_url=match_url,
                home=home,
                away=away,
                date_str=date_str,
                competition_code=competition_code,
            )
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

        enrichment: EnrichmentFetchResult = fetch_enrichment_for_facts(
            facts,
            openclaw_url=self._openclaw_url,
            openclaw_api_key=self._openclaw_api_key,
            skip_openclaw=self._skip_openclaw,
            odds_url=self._odds_url,
            odds_api_key=self._odds_api_key,
            skip_odds=self._skip_odds,
            home_override=home,
            away_override=away,
            date_override=date_str,
            competition_override=competition_code,
            match_url_override=match_url,
        )
        oc_ctx = enrichment.context
        odds_ctx = enrichment.odds
        sources.update(enrichment.sources)
        warnings: List[str] = list(enrichment.warnings)
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
            snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
            scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)
            scored, competition_guardrail = apply_competition_guardrails(scored, competition_clf)
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
