"""Merge pending clarification context with follow-up user messages."""

from __future__ import annotations

import re
from typing import Optional

from football_agent.bot.clarification_state import PendingClarification
from football_agent.bot.request_parser import extract_date

_OPPONENT_FOLLOWUP_RE = re.compile(
    r"(?is)(?:с|против)\s+(.+?)(?:\s+на\s+(?:завтра|сегодня)|\s*$)",
)
_LEAGUE_PREFIX_RE = re.compile(
    r"(?is)^(?:на\s+)?(?:лигу|чемпионат|турнир)\s+",
)


def merge_clarification_text(pending: PendingClarification, new_text: str) -> str:
    """
    Combine prior vague query with user's follow-up.

    Examples:
    - pending "дай прогноз" + "на лигу Китая" → league phrase
    - pending home="интер" + "с миланом завтра" → "интер — милан" + date
    """
    new = (new_text or "").strip()
    if not new:
        return pending.partial_text

    if pending.intent == "league":
        return _merge_league_followup(pending, new)

    if pending.intent == "match":
        merged = _merge_match_followup(pending, new)
        if merged:
            return merged

    if pending.intent == "any":
        if pending.partial_text and _looks_like_followup_fragment(new):
            combined = f"{pending.partial_text} {new}".strip()
            return combined

    return new


def _merge_league_followup(pending: PendingClarification, new: str) -> str:
    phrase = new
    if _LEAGUE_PREFIX_RE.match(phrase):
        phrase = _LEAGUE_PREFIX_RE.sub("", phrase).strip()
    if pending.partial_text:
        low_pending = pending.partial_text.lower()
        if "лиг" in low_pending or "тур" in low_pending or "чемпионат" in low_pending:
            return f"дай прогноз на лигу {phrase}".strip()
        return f"{pending.partial_text} {new}".strip()
    return f"дай прогноз на лигу {phrase}".strip()


def _merge_match_followup(pending: PendingClarification, new: str) -> Optional[str]:
    home = (pending.partial_home or "").strip()
    if not home:
        if pending.partial_text:
            combined = f"{pending.partial_text} {new}".strip()
            return combined
        return None

    away: Optional[str] = None
    m = _OPPONENT_FOLLOWUP_RE.search(new)
    if m:
        away = m.group(1).strip(" .,!?:;")
    elif " — " in new or " - " in new or " vs " in new.lower():
        return new

    if away:
        date_str = extract_date(new)
        line = f"{home} — {away}"
        if date_str:
            line = f"{line} {date_str}"
        return line

    if pending.partial_text:
        return f"{pending.partial_text} {new}".strip()
    return None


def _looks_like_followup_fragment(text: str) -> bool:
    low = text.lower()
    if low.startswith(("на ", "с ", "против ")):
        return True
    if _LEAGUE_PREFIX_RE.match(text):
        return True
    if "лиг" in low or "тур" in low:
        return True
    return len(text.split()) <= 6
