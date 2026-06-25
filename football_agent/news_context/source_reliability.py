"""Source reliability tiers for Brave news hits."""

from __future__ import annotations

import re
from typing import Tuple

from football_agent.news_context.models import ReliabilityLevel

_DOMAIN_TIERS: list[tuple[re.Pattern[str], ReliabilityLevel, float]] = [
    (re.compile(r"ge\.globo\.com", re.I), "HIGH", 1.0),
    (re.compile(r"otempo\.com\.br", re.I), "HIGH", 0.95),
    (re.compile(r"gazetaesportiva\.com", re.I), "HIGH", 0.9),
    (re.compile(r"futebolinterior\.com\.br", re.I), "MEDIUM", 0.8),
    (re.compile(r"uol\.com\.br", re.I), "MEDIUM", 0.75),
    (re.compile(r"ogol\.com", re.I), "MEDIUM", 0.65),
    (re.compile(r"sportytrader|betfair|apostas\.|palpites|tips\.|oddspedia|betano", re.I), "LOW", 0.3),
    (re.compile(r"minuto a minuto|ao vivo", re.I), "LOW", 0.25),
]


def source_reliability(url: str | None, source_name: str | None = None) -> Tuple[ReliabilityLevel, float]:
    blob = f"{url or ''} {source_name or ''}"
    for pat, level, weight in _DOMAIN_TIERS:
        if pat.search(blob):
            return level, weight
    if url and "://" in url:
        return "LOW", 0.4
    return "UNKNOWN", 0.2


def hit_rank_key(*, url: str | None, source_name: str | None, title: str, topic_tag: str | None) -> tuple:
    _level, weight = source_reliability(url, source_name)
    category_rank = {
        "injuries": 0,
        "lineup": 1,
        "coach": 2,
        "coach_profile": 3,
        "preview": 4,
        "rotation": 5,
        "h2h": 6,
    }.get(topic_tag or "", 7)
    return (-weight, category_rank, (title or "").lower())
