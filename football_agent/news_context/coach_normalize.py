"""Normalize coach name strings extracted from Brave / news snippets."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

_COACH_PREFIX_RE = re.compile(
    r"^(?:t[eé]cnico|coach|manager|head coach|treinador)\s+",
    re.I,
)

_COACH_TRAILING_STOPWORDS = frozenset(
    {
        "terá",
        "tera",
        "téra",
        "pode",
        "precisa",
        "said",
        "says",
        "disse",
        "falou",
        "confirmou",
        "anunciou",
        "vai",
        "foi",
        "está",
        "esta",
        "será",
        "sera",
        "deve",
        "tinha",
        "tem",
        "had",
        "will",
        "has",
        "is",
        "was",
    },
)


def fold_text(value: str) -> str:
    nfd = unicodedata.normalize("NFD", value or "")
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()


def normalize_coach_name(raw: Optional[str]) -> Optional[str]:
    """Strip role prefixes and trailing PT/EN verbs; return 'First Last' or None."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", str(raw))
    text = _COACH_PREFIX_RE.sub("", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" ,.;:")
    if not text:
        return None

    parts: list[str] = []
    for token in text.split():
        low = fold_text(token)
        if low in _COACH_TRAILING_STOPWORDS:
            break
        if len(low) <= 1:
            continue
        parts.append(token)
        if len(parts) >= 3:
            break

    if not parts:
        return None
    if len(parts) == 1 and len(parts[0]) < 3:
        return None
    return " ".join(parts)
