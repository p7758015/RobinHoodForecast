"""In-memory pending clarification state per Telegram chat (pragmatic, no DB)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from football_agent import config
from football_agent.bot.request_parser import ClarificationReason, MatchRequestKind


@dataclass
class PendingClarification:
    chat_id: int
    intent: str  # "any" | "match" | "league"
    reason: ClarificationReason
    partial_text: str = ""
    partial_home: Optional[str] = None
    partial_away: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class ClarificationStateStore:
    """One pending clarification per chat; expires after TTL."""

    def __init__(self, ttl_s: Optional[float] = None) -> None:
        self._ttl_s = ttl_s if ttl_s is not None else config.TELEGRAM_CLARIFICATION_TTL_S
        self._pending: Dict[int, PendingClarification] = {}

    def get_valid(self, chat_id: int) -> Optional[PendingClarification]:
        pending = self._pending.get(chat_id)
        if pending is None:
            return None
        if time.time() - pending.created_at > self._ttl_s:
            del self._pending[chat_id]
            return None
        return pending

    def set_from_request(
        self,
        chat_id: int,
        *,
        reason: ClarificationReason,
        raw_text: str,
        partial_home: Optional[str] = None,
        partial_away: Optional[str] = None,
    ) -> PendingClarification:
        intent = _intent_for_reason(reason)
        pending = PendingClarification(
            chat_id=chat_id,
            intent=intent,
            reason=reason,
            partial_text=raw_text.strip(),
            partial_home=partial_home,
            partial_away=partial_away,
        )
        self._pending[chat_id] = pending
        return pending

    def clear(self, chat_id: int) -> None:
        self._pending.pop(chat_id, None)


def _intent_for_reason(reason: ClarificationReason) -> str:
    if reason in (
        ClarificationReason.MISSING_LEAGUE,
        ClarificationReason.AMBIGUOUS_LEAGUE,
    ):
        return "league"
    if reason in (
        ClarificationReason.MISSING_MATCH_TEAMS,
        ClarificationReason.MISSING_OPPONENT,
        ClarificationReason.AMBIGUOUS_TEAMS,
    ):
        return "match"
    return "any"


def should_reset_pending_on_message(text: str) -> bool:
    """User explicitly starts over (URL, /help, cancel)."""
    low = (text or "").strip().lower()
    if not low:
        return False
    if low.startswith("/"):
        return True
    if "flashscore.com/match" in low:
        return True
    if low in ("отмена", "cancel", "стоп", "stop"):
        return True
    return False
