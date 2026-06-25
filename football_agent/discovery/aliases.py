"""
Static query aliases for competition resolution (fast path, no network).

Registry remains the primary accelerator; aliases cover common user phrases
before generic Flashscore search.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# normalized_query -> (canonical English name, country hint)
STATIC_COMPETITION_ALIASES: Dict[str, Tuple[str, Optional[str]]] = {
    "лига китая": ("Chinese Super League", "China"),
    "liga kitaya": ("Chinese Super League", "China"),
    "china league": ("Chinese Super League", "China"),
    "chinese super league": ("Chinese Super League", "China"),
    "china super league": ("Chinese Super League", "China"),
    "csl": ("Chinese Super League", "China"),
    "казахстан премьер лига": ("Premier League", "Kazakhstan"),
    "kazakhstan premier league": ("Premier League", "Kazakhstan"),
    "kazakhstan premier": ("Premier League", "Kazakhstan"),
    "latvia virsliga": ("Virsliga", "Latvia"),
    "virsliga": ("Virsliga", "Latvia"),
    "estonia meistriliiga": ("Meistriliiga", "Estonia"),
    "meistriliiga": ("Meistriliiga", "Estonia"),
    "premium liiga": ("Premium Liiga", "Estonia"),
    "brazil serie b": ("Serie B", "Brazil"),
    "serie b brazil": ("Serie B", "Brazil"),
    "brasileirao serie b": ("Serie B", "Brazil"),
    "ireland premier division": ("Premier Division", "Ireland"),
    "league of ireland": ("Premier Division", "Ireland"),
    "premier division ireland": ("Premier Division", "Ireland"),
    "ireland premier": ("Premier Division", "Ireland"),
}


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def lookup_static_alias(query: str) -> Optional[Tuple[str, Optional[str]]]:
    key = _norm(query)
    if not key:
        return None
    if key in STATIC_COMPETITION_ALIASES:
        return STATIC_COMPETITION_ALIASES[key]
    for alias, value in STATIC_COMPETITION_ALIASES.items():
        if alias in key or key in alias:
            return value
    return None
