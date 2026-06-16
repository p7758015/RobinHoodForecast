"""
League-level Telegram analysis via competition discovery + per-match pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from football_agent import config
from football_agent.bot.clarification_messages import format_ambiguous_league_reply
from football_agent.bot.request_parser import ParsedMatchRequest, default_league_date_range, league_period_note
from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.discovery.models import DiscoveredFixture
from football_agent.output.telegram_league_output import format_teleague_league_reply
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline, LivePipelineResult

logger = logging.getLogger(__name__)


@dataclass
class LeagueAnalysisOutcome:
    success: bool
    reply_text: str
    competition_name: Optional[str] = None
    fixtures_total: int = 0
    analyzed: int = 0
    results: List[LivePipelineResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stage_failed: Optional[str] = None
    needs_clarification: bool = False


class TelegramLeagueAnalysisService:
    """
    resolve_competition → list fixtures → LiveFlashscorePipeline per match_url.
    """

    def __init__(
        self,
        *,
        pipeline: Optional[LiveFlashscorePipeline] = None,
        resolver: Optional[CompetitionResolverService] = None,
        fixture_svc: Optional[FixtureDiscoveryService] = None,
        max_matches: Optional[int] = None,
        pipeline_runner: Optional[Callable[[str], LivePipelineResult]] = None,
    ) -> None:
        self._pipeline = pipeline or LiveFlashscorePipeline()
        self._resolver = resolver or CompetitionResolverService()
        self._fixture_svc = fixture_svc or FixtureDiscoveryService(resolver=self._resolver)
        self._max_matches = max_matches or config.TELEGRAM_LEAGUE_MAX_MATCHES
        self._pipeline_runner = pipeline_runner

    def analyze_league_request(self, request: ParsedMatchRequest):
        from football_agent.services.telegram_match_analysis_service import TelegramAnalysisResponse

        phrase = (request.league_phrase or "").strip()
        if not phrase:
            return TelegramAnalysisResponse(
                reply_text="Не указана лига для анализа.",
                success=False,
                request_kind="league_query",
                stage_failed="missing_league_phrase",
                needs_clarification=True,
            )

        outcome = self._run_league_analysis(phrase, request)
        return TelegramAnalysisResponse(
            reply_text=outcome.reply_text,
            success=outcome.success,
            request_kind="league_query",
            analysis_path="league_discovery",
            persisted=any(r.persisted for r in outcome.results),
            stage_failed=outcome.stage_failed,
            warnings=list(outcome.warnings),
            needs_clarification=outcome.needs_clarification,
        )

    def _run_league_analysis(self, phrase: str, request: ParsedMatchRequest) -> LeagueAnalysisOutcome:
        warnings: List[str] = []
        resolve = self._resolver.resolve_competition(phrase, allow_ambiguous=False)

        if resolve.resolved is None:
            if resolve.ambiguous and resolve.candidates:
                return LeagueAnalysisOutcome(
                    success=False,
                    reply_text=format_ambiguous_league_reply(resolve.candidates),
                    warnings=list(resolve.warnings) + ["ambiguous"],
                    stage_failed="ambiguous_competition",
                    needs_clarification=True,
                )
            return LeagueAnalysisOutcome(
                success=False,
                reply_text=(
                    "Не удалось найти такую лигу на Flashscore.\n\n"
                    "Уточните название и страну, например:\n"
                    "• дай прогноз на лигу Китая\n"
                    "• дай прогноз на Virsliga Latvia"
                ),
                warnings=list(resolve.warnings) + ["not_found"],
                stage_failed="competition_not_found",
                needs_clarification=True,
            )

        resolved = resolve.resolved
        candidate = resolved.candidate

        date_from, date_to = default_league_date_range(request)
        period_note = league_period_note(request, date_from, date_to)
        fixture_result = self._fixture_svc.list_competition_fixtures(
            resolved,
            date_from=date_from,
            date_to=date_to,
        )
        warnings.extend(fixture_result.warnings)

        fixtures = fixture_result.fixtures
        if not fixtures:
            period = date_from if date_from == date_to else f"{date_from} — {date_to}"
            note = f" ({period_note})" if period_note else ""
            return LeagueAnalysisOutcome(
                success=False,
                reply_text=(
                    f"Лига «{candidate.competition_name}» найдена, "
                    f"но матчей на {period}{note} нет.\n\n"
                    "Уточните даты, например:\n"
                    f"• дай прогноз на {phrase} на 2026-06-15"
                ),
                competition_name=candidate.competition_name,
                warnings=warnings,
                stage_failed="no_fixtures",
            )

        fixtures = self._sort_fixtures(fixtures)[: self._max_matches]
        results: List[LivePipelineResult] = []
        for fx in fixtures:
            if not fx.match_url:
                warnings.append(f"missing_url:{fx.home_team}-{fx.away_team}")
                continue
            try:
                if self._pipeline_runner:
                    result = self._pipeline_runner(fx.match_url)
                else:
                    result = self._pipeline.analyze_flashscore_url(fx.match_url)
            except Exception as exc:
                logger.exception("league pipeline failed url=%s", fx.match_url)
                warnings.append(f"pipeline_error:{exc}")
                continue
            results.append(result)

        analyzed_ok = [r for r in results if r.success]
        reply = format_teleague_league_reply(
            competition_name=candidate.competition_name,
            competition_country=candidate.country,
            date_from=date_from,
            date_to=date_to,
            results=results,
            period_note=period_note,
            truncated=len(fixture_result.fixtures) > self._max_matches,
            max_shown=self._max_matches,
        )

        return LeagueAnalysisOutcome(
            success=bool(analyzed_ok),
            reply_text=reply,
            competition_name=candidate.competition_name,
            fixtures_total=len(fixture_result.fixtures),
            analyzed=len(analyzed_ok),
            results=results,
            warnings=warnings,
            stage_failed=None if analyzed_ok else "all_matches_failed",
        )

    @staticmethod
    def _sort_fixtures(fixtures: List[DiscoveredFixture]) -> List[DiscoveredFixture]:
        return sorted(
            fixtures,
            key=lambda f: (f.match_date or "9999", f.kickoff_utc or ""),
        )
