"""
Application service: neutral user request → single-match analysis result.

No Telegram imports — safe for CLI, workers, and future webhook handlers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from football_agent.bot.request_parser import (
    MatchRequestKind,
    ParsedMatchRequest,
    default_match_date,
    parse_match_request,
)
from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline, LivePipelineResult

logger = logging.getLogger(__name__)


@dataclass
class TelegramAnalysisResponse:
    """Structured outcome for any transport layer (Telegram, HTTP, CLI)."""

    reply_text: str
    success: bool
    request_kind: str
    analysis_path: Optional[str] = None
    persisted: bool = False
    run_id: Optional[str] = None
    match_key: Optional[str] = None
    stage_failed: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)


class TelegramMatchAnalysisService:
    """
    Orchestrates single-match analysis for bot users.

    Supported inputs:
    - Flashscore match URL (primary)
    - ``Home - Away`` with optional date (Flashscore date listing + team resolver)
    """

    def __init__(self, pipeline: Optional[LiveFlashscorePipeline] = None) -> None:
        self._pipeline = pipeline or LiveFlashscorePipeline()

    def analyze_text(self, user_text: str) -> TelegramAnalysisResponse:
        request = parse_match_request(user_text)
        logger.info(
            "Match analysis request kind=%s text=%r",
            request.kind.value,
            request.raw_text[:200],
        )

        if request.kind == MatchRequestKind.UNSUPPORTED:
            return TelegramAnalysisResponse(
                reply_text=_unsupported_help_text(),
                success=False,
                request_kind=request.kind.value,
                stage_failed="unsupported_input",
            )

        if request.kind == MatchRequestKind.FLASHSCORE_URL:
            result = self._pipeline.analyze_flashscore_url(request.flashscore_url or "")
        else:
            date_str = default_match_date(request)
            result = self._pipeline.analyze_teams(
                request.home_team or "",
                request.away_team or "",
                date_str,
            )

        return self._to_response(request, result)

    def _to_response(
        self,
        request: ParsedMatchRequest,
        result: LivePipelineResult,
    ) -> TelegramAnalysisResponse:
        if not result.success:
            logger.warning(
                "Analysis failed kind=%s path=%s stage=%s sources=%s",
                request.kind.value,
                result.path,
                result.stage_failed,
                result.sources,
            )
            return TelegramAnalysisResponse(
                reply_text=result.user_message or "Не удалось выполнить анализ.",
                success=False,
                request_kind=request.kind.value,
                analysis_path=result.path,
                persisted=False,
                stage_failed=result.stage_failed,
                warnings=list(result.warnings),
                sources=dict(result.sources),
            )

        reply = format_telegram_match_reply(result)
        logger.info(
            "Analysis ok kind=%s path=%s persisted=%s run_id=%s match_key=%s warnings=%d",
            request.kind.value,
            result.path,
            result.persisted,
            result.run_id,
            result.match_key,
            len(result.warnings),
        )
        return TelegramAnalysisResponse(
            reply_text=reply,
            success=True,
            request_kind=request.kind.value,
            analysis_path=result.path,
            persisted=result.persisted,
            run_id=result.run_id,
            match_key=result.match_key,
            warnings=list(result.warnings),
            sources=dict(result.sources),
        )


def _unsupported_help_text() -> str:
    return (
        "Не понял запрос.\n\n"
        "Поддерживается:\n"
        "• ссылка на матч Flashscore\n"
        "• команды через «—» или «vs»\n\n"
        "Примеры:\n"
        "https://www.flashscore.com/match/football/.../?mid=...\n"
        "FAR Rabat — Maghreb Fez\n"
        "Real Madrid vs Barcelona 2026-06-15"
    )
