"""
Parse Telegram user text into neutral match-analysis requests.

Transport-agnostic — no Telegram imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


FLASHSCORE_URL_RE = re.compile(
    r"https?://(?:www\.)?flashscore\.[a-z.]+/match/[^\s]+",
    re.IGNORECASE,
)

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DATE_DMY_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")

TEAM_SEPARATORS = (" vs ", " v ", " — ", " - ", " – ", " против ")


class MatchRequestKind(str, Enum):
    FLASHSCORE_URL = "flashscore_url"
    TEAM_QUERY = "team_query"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ParsedMatchRequest:
    kind: MatchRequestKind
    raw_text: str
    flashscore_url: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    date_str: Optional[str] = None


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


def parse_match_request(text: str) -> ParsedMatchRequest:
    """
    Classify user text for single-match Telegram analysis.

    Priority:
    1. Flashscore match URL
    2. ``Home - Away`` (optional date in text; defaults to today at service layer)
    3. unsupported
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedMatchRequest(kind=MatchRequestKind.UNSUPPORTED, raw_text=raw)

    url_match = FLASHSCORE_URL_RE.search(raw)
    if url_match:
        return ParsedMatchRequest(
            kind=MatchRequestKind.FLASHSCORE_URL,
            raw_text=raw,
            flashscore_url=url_match.group(0),
        )

    date_str = _extract_date(raw)
    team_text = raw
    if date_str:
        team_text = DATE_RE.sub(" ", team_text)
        team_text = DATE_DMY_RE.sub(" ", team_text).strip()

    home, away = _parse_team_pair(team_text)
    if home and away:
        return ParsedMatchRequest(
            kind=MatchRequestKind.TEAM_QUERY,
            raw_text=raw,
            home_team=home,
            away_team=away,
            date_str=date_str,
        )

    return ParsedMatchRequest(kind=MatchRequestKind.UNSUPPORTED, raw_text=raw)


def default_match_date(request: ParsedMatchRequest) -> str:
    """Resolve analysis date for team-query mode."""
    if request.date_str:
        return request.date_str
    return date.today().isoformat()
