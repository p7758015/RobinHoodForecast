"""
Parse Telegram user text into neutral match-analysis requests.

Transport-agnostic — no Telegram imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional


FLASHSCORE_URL_RE = re.compile(
    r"https?://(?:www\.)?flashscore\.[a-z.]+/match/[^\s]+",
    re.IGNORECASE,
)

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DATE_DMY_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")

TEAM_SEPARATORS = (" vs ", " v ", " — ", " - ", " – ", " против ")

# League-level intents (checked before team-pair parsing).
_LEAGUE_COMMAND_RE = re.compile(
    r"(?is)"
    r"(?:дай(?:те)?\s+)?(?:прогноз|анализ|проанализируй|проанализируйте)\s+"
    r"(?:на\s+)?(?:(?:лигу|чемпионат|турнир|серию)\s+)?(.+?)\s*$"
    r"|"
    r"(?:проанализируй|проанализируйте|анализ)\s+"
    r"(?:(?:следующий|ближайший)\s+тур\s+)?(.+?)\s*$",
)

_LEAGUE_HINT_RE = re.compile(
    r"(?i)\b(лиг[ауеи]|чемпионат|турнир|serie\s+[ab]|premier\s+league|super\s+league|"
    r"virsliga|meistriliiga|brasileirao|серию\s+[ab]|казахстан|латви|эстони|китая|китай)\b",
)

_VAGUE_COMMAND_RE = re.compile(
    r"(?i)^(?:дай(?:те)?\s+)?(?:прогноз|анализ|что\s+думаешь|мнение|совет)(?:\s+на)?\s*$",
)
_LEAGUE_SHELL_RE = re.compile(
    r"(?i)^(?:дай(?:те)?\s+)?(?:прогноз|анализ)\s+на\s+(?:лигу|чемпионат|турнир)\s*$",
)
_ROUND_SHELL_RE = re.compile(
    r"(?i)^(?:проанализируй|проанализируйте|анализ)\s+(?:следующий|ближайший)?\s*тур\s*$",
)
_NEXT_ROUND_NO_LEAGUE_RE = re.compile(
    r"(?i)^(?:дай(?:те)?\s+)?(?:прогноз|анализ)\s+на\s+(?:следующий|ближайший)\s+тур\s*$",
)
_FORECAST_ON_TEAM_RE = re.compile(
    r"(?i)(?:дай(?:те)?\s+)?(?:прогноз|анализ)\s+на\s+(.+?)\s*$",
)
_DATE_VAGUE_RE = re.compile(
    r"(?i)\b(на\s+выходных|на\s+следующей\s+неделе|завтра\s+и\s+послезавтра|"
    r"на\s+этой\s+неделе|в\s+ближайшие\s+дни)\b",
)
_COUNTRY_ONLY_RE = re.compile(
    r"(?i)^(?:дай(?:те)?\s+)?(?:прогноз|анализ)?\s*(?:на\s+)?"
    r"(китай|китая|латви[юя]|эстони[юя]|казахстан|бразили[юя])\s*$",
)
_LEAGUE_SHELL_PHRASE_RE = re.compile(
    r"(?i)^(лиг[ауеи]?|чемпионат|турнир|сери[юя]|serie\s+[ab])\s*$",
)


class MatchRequestKind(str, Enum):
    FLASHSCORE_URL = "flashscore_url"
    TEAM_QUERY = "team_query"
    LEAGUE_QUERY = "league_query"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class ClarificationReason(str, Enum):
    TOO_VAGUE = "too_vague"
    MISSING_LEAGUE = "missing_league"
    MISSING_MATCH_TEAMS = "missing_match_teams"
    MISSING_OPPONENT = "missing_opponent"
    AMBIGUOUS_TEAMS = "ambiguous_teams"
    DATE_AMBIGUOUS = "date_ambiguous"
    AMBIGUOUS_LEAGUE = "ambiguous_league"


@dataclass(frozen=True)
class ParsedMatchRequest:
    kind: MatchRequestKind
    raw_text: str
    flashscore_url: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    date_str: Optional[str] = None
    league_phrase: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    clarification_reason: Optional[ClarificationReason] = None
    partial_home: Optional[str] = None


def extract_date(text: str) -> Optional[str]:
    return _extract_date(text)


def _extract_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if m:
        return m.group(1)
    m = DATE_DMY_RE.search(text)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return None


def _strip_url_from_text(text: str, url: str) -> str:
    return text.replace(url, " ").strip()


def _parse_team_pair(text: str) -> tuple[Optional[str], Optional[str]]:
    cleaned = text.strip()
    if not cleaned:
        return None, None

    for sep in TEAM_SEPARATORS:
        if sep in cleaned:
            parts = cleaned.split(sep, 1)
            if len(parts) == 2:
                home = parts[0].strip(" \"'«»")
                away = parts[1].strip(" \"'«»")
                if home and away:
                    return home, away
    return None, None


def _clean_league_phrase(phrase: str) -> str:
    text = phrase.strip(" .,!?:;\"'«»")
    text = re.sub(r"(?i)\b(на\s+сегодня|на\s+завтра|ближайшие\s+матчи)\b", "", text).strip()
    return text


def _try_parse_league_query(text: str) -> Optional[ParsedMatchRequest]:
    raw = text.strip()
    if not raw or FLASHSCORE_URL_RE.search(raw):
        return None

    if _DATE_VAGUE_RE.search(raw) and not _extract_date(raw):
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.DATE_AMBIGUOUS,
        )

    date_str = _extract_date(raw)
    date_from = date_str
    date_to = date_str

    m = _LEAGUE_COMMAND_RE.search(raw)
    phrase: Optional[str] = None
    if m:
        phrase = _clean_league_phrase(m.group(1) or "")
    elif _LEAGUE_HINT_RE.search(raw) and not _parse_team_pair(raw)[0]:
        phrase = _clean_league_phrase(raw)
        for prefix in (
            r"(?i)^дай(?:те)?\s+прогноз\s+на\s+",
            r"(?i)^прогноз\s+на\s+",
            r"(?i)^анализ\s+",
        ):
            phrase = re.sub(prefix, "", phrase).strip()

    if not phrase or len(phrase) < 3:
        return None
    if _LEAGUE_SHELL_PHRASE_RE.match(phrase.strip()):
        return None
    if not _LEAGUE_HINT_RE.search(raw) and len(phrase.split()) < 2:
        return None
    if _parse_team_pair(phrase)[0]:
        return None

    return ParsedMatchRequest(
        kind=MatchRequestKind.LEAGUE_QUERY,
        raw_text=raw,
        league_phrase=phrase,
        date_str=date_str,
        date_from=date_from,
        date_to=date_to,
    )


def _try_parse_clarification(text: str) -> Optional[ParsedMatchRequest]:
    raw = text.strip()
    if not raw:
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.TOO_VAGUE,
        )

    if _VAGUE_COMMAND_RE.match(raw):
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.TOO_VAGUE,
        )

    if _LEAGUE_SHELL_RE.match(raw) or _ROUND_SHELL_RE.match(raw) or _NEXT_ROUND_NO_LEAGUE_RE.match(raw):
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.MISSING_LEAGUE,
        )

    if _COUNTRY_ONLY_RE.match(raw):
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.MISSING_LEAGUE,
        )

    if _DATE_VAGUE_RE.search(raw) and not _extract_date(raw):
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.DATE_AMBIGUOUS,
        )

    fm = _FORECAST_ON_TEAM_RE.match(raw)
    if fm:
        target = (fm.group(1) or "").strip()
        if target and not _LEAGUE_HINT_RE.search(target):
            home, away = _parse_team_pair(target)
            if not home:
                words = target.split()
                if 1 <= len(words) <= 3 and not _extract_date(target):
                    return ParsedMatchRequest(
                        kind=MatchRequestKind.NEEDS_CLARIFICATION,
                        raw_text=raw,
                        clarification_reason=ClarificationReason.MISSING_OPPONENT,
                        partial_home=target,
                    )

    # Two tokens without separator: "арсенал челси"
    if not _LEAGUE_HINT_RE.search(raw):
        tokens = raw.split()
        if len(tokens) == 2 and len(raw) < 60:
            low = raw.lower()
            if not any(sep.strip() in f" {low} " for sep in TEAM_SEPARATORS):
                if not low.startswith(("http", "www")):
                    return ParsedMatchRequest(
                        kind=MatchRequestKind.NEEDS_CLARIFICATION,
                        raw_text=raw,
                        clarification_reason=ClarificationReason.AMBIGUOUS_TEAMS,
                    )

    # Single short token without context: "реал"
    if len(raw) < 25 and " " not in raw.strip() and not raw.startswith("http"):
        if not _LEAGUE_HINT_RE.search(raw):
            return ParsedMatchRequest(
                kind=MatchRequestKind.NEEDS_CLARIFICATION,
                raw_text=raw,
                clarification_reason=ClarificationReason.MISSING_OPPONENT,
                partial_home=raw,
            )

    return None


def parse_match_request(text: str) -> ParsedMatchRequest:
    """
    Classify user text for Telegram analysis.

    Priority:
    1. Flashscore match URL
    2. League-level query (discovery path)
    3. ``Home - Away`` (optional date)
    4. needs clarification (vague / incomplete)
    5. unsupported
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedMatchRequest(
            kind=MatchRequestKind.NEEDS_CLARIFICATION,
            raw_text=raw,
            clarification_reason=ClarificationReason.TOO_VAGUE,
        )

    url_match = FLASHSCORE_URL_RE.search(raw)
    if url_match:
        return ParsedMatchRequest(
            kind=MatchRequestKind.FLASHSCORE_URL,
            raw_text=raw,
            flashscore_url=url_match.group(0),
        )

    clarify_early = _try_parse_clarification(raw)
    if clarify_early is not None and clarify_early.clarification_reason in (
        ClarificationReason.TOO_VAGUE,
        ClarificationReason.MISSING_LEAGUE,
        ClarificationReason.DATE_AMBIGUOUS,
    ):
        return clarify_early

    league_req = _try_parse_league_query(raw)
    if league_req is not None:
        return league_req

    date_str = _extract_date(raw)
    team_text = raw
    if date_str:
        team_text = DATE_RE.sub(" ", team_text)
        team_text = DATE_DMY_RE.sub(" ", team_text).strip()

    home, away = _parse_team_pair(team_text)
    if home and away:
        if _DATE_VAGUE_RE.search(raw) and not date_str:
            return ParsedMatchRequest(
                kind=MatchRequestKind.NEEDS_CLARIFICATION,
                raw_text=raw,
                clarification_reason=ClarificationReason.DATE_AMBIGUOUS,
            )
        return ParsedMatchRequest(
            kind=MatchRequestKind.TEAM_QUERY,
            raw_text=raw,
            home_team=home,
            away_team=away,
            date_str=date_str,
        )

    clarify = _try_parse_clarification(raw)
    if clarify is not None:
        return clarify

    return ParsedMatchRequest(
        kind=MatchRequestKind.NEEDS_CLARIFICATION,
        raw_text=raw,
        clarification_reason=ClarificationReason.MISSING_MATCH_TEAMS,
    )


def default_league_date_range(request: ParsedMatchRequest) -> tuple[str, str]:
    """When user omits dates for a league query, use today .. today+6."""
    if request.date_from:
        end = request.date_to or request.date_from
        return request.date_from, end
    start = date.today()
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def default_match_date(request: ParsedMatchRequest) -> str:
    """Resolve analysis date for team-query mode."""
    if request.date_str:
        return request.date_str
    return date.today().isoformat()


def league_period_note(request: ParsedMatchRequest, date_from: str, date_to: str) -> Optional[str]:
    """Explain default period when user omitted explicit dates."""
    if request.date_from:
        return None
    if date_from == date_to:
        return f"дата не указана — взят один день: {date_from}"
    return f"дата не указана — ближайшие 7 дней: {date_from} — {date_to}"
