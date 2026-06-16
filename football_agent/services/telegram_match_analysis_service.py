"""
Application service: neutral user request → single-match analysis result.

No Telegram imports — safe for CLI, workers, and future webhook handlers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from football_agent.bot.clarification_flow import merge_clarification_text
from football_agent.bot.clarification_messages import format_clarification_reply
from football_agent.bot.clarification_state import ClarificationStateStore, should_reset_pending_on_message
from football_agent.bot.request_parser import (
    ClarificationReason,
    MatchRequestKind,
    ParsedMatchRequest,
    default_match_date,
    parse_match_request,
)
from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline, LivePipelineResult
from football_agent.services.telegram_league_analysis_service import TelegramLeagueAnalysisService

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
    needs_clarification: bool = False


class TelegramMatchAnalysisService:
    """
    Orchestrates match/league analysis for bot users with clarification flow.
    """

    def __init__(
        self,
        pipeline: Optional[LiveFlashscorePipeline] = None,
        league_service: Optional[TelegramLeagueAnalysisService] = None,
        clarification_store: Optional[ClarificationStateStore] = None,
    ) -> None:
        self._pipeline = pipeline or LiveFlashscorePipeline()
        self._league_service = league_service or TelegramLeagueAnalysisService(pipeline=self._pipeline)
        self._clarification_store = clarification_store or ClarificationStateStore()

    def analyze_text(self, user_text: str, *, chat_id: Optional[int] = None) -> TelegramAnalysisResponse:
        if chat_id is not None and should_reset_pending_on_message(user_text):
            self._clarification_store.clear(chat_id)

        effective_text = user_text
        pending = None
        if chat_id is not None:
            pending = self._clarification_store.get_valid(chat_id)
            if pending is not None:
                effective_text = merge_clarification_text(pending, user_text)
                logger.info(
                    "clarification_merge chat_id=%s pending_reason=%s merged=%r",
                    chat_id,
                    pending.reason.value,
                    effective_text[:200],
                )

        request = parse_match_request(effective_text)
        logger.info(
            "Match analysis request kind=%s text=%r effective=%r",
            request.kind.value,
            user_text[:200],
            effective_text[:200],
        )

        if request.kind == MatchRequestKind.NEEDS_CLARIFICATION:
            return self._clarification_response(request, chat_id=chat_id)

        if chat_id is not None:
            self._clarification_store.clear(chat_id)

        if request.kind == MatchRequestKind.LEAGUE_QUERY:
            resp = self._league_service.analyze_league_request(request)
            if resp.needs_clarification and chat_id is not None:
                self._clarification_store.set_from_request(
                    chat_id,
                    reason=ClarificationReason.AMBIGUOUS_LEAGUE,
                    raw_text=user_text,
                )
            return resp

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

    def _clarification_response(
        self,
        request: ParsedMatchRequest,
        *,
        chat_id: Optional[int],
    ) -> TelegramAnalysisResponse:
        reason = request.clarification_reason
        if reason is None:
            reply = format_clarification_reply(ClarificationReason.MISSING_MATCH_TEAMS)
        else:
            reply = format_clarification_reply(reason)

        if chat_id is not None and reason is not None:
            self._clarification_store.set_from_request(
                chat_id,
                reason=reason,
                raw_text=request.raw_text,
                partial_home=request.partial_home,
            )

        return TelegramAnalysisResponse(
            reply_text=reply,
            success=False,
            request_kind=MatchRequestKind.NEEDS_CLARIFICATION.value,
            stage_failed=reason.value if reason else "needs_clarification",
            needs_clarification=True,
        )

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
