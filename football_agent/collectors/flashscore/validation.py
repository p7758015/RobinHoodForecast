"""Flashscore field validation helpers."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

_UNKNOWN_TOKENS = frozenset({"unknown", "n/a", "na", "tbd", "-", ""})

# Sentence-like / news-fragment competition names (Serie B scraper bug pattern).
_JUNK_COMPETITION_PATTERNS = (
    re.compile(r"https?://", re.I),
    re.compile(r"\bnews\b", re.I),
    re.compile(r"\btransfer", re.I),
    re.compile(r"\btoday\b", re.I),
    re.compile(r"\blatest\b", re.I),
    re.compile(r"[.!?]{2,}"),
)


def normalize_team_name(value: Optional[str]) -> str:
    return (value or "").strip()


def is_valid_team_name(value: Optional[str]) -> bool:
    name = normalize_team_name(value)
    if not name:
        return False
    if name.lower() in _UNKNOWN_TOKENS:
        return False
    if len(name) < 2:
        return False
    return True


def is_valid_competition_name(value: Optional[str]) -> Tuple[bool, List[str]]:
    name = (value or "").strip()
    warnings: List[str] = []
    if not name:
        warnings.append("competition_name_empty")
        return False, warnings
    lower = name.lower()
    if lower in _UNKNOWN_TOKENS or lower == "unknown competition":
        warnings.append("competition_name_unknown")
        return False, warnings
    if len(name) > 80:
        warnings.append("competition_name_too_long")
        return False, warnings
    if name.count(" ") > 8:
        warnings.append("competition_name_sentence_like")
        return False, warnings
    for pat in _JUNK_COMPETITION_PATTERNS:
        if pat.search(name):
            warnings.append("competition_name_junk_pattern")
            return False, warnings
    return True, warnings
