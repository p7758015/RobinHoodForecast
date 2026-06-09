"""
Team name normalization and alias resolution for match lookup.

Designed for league v2 single-match queries (EN partial names, RU variants).
Future OpenClaw feeds can reuse the same normalization entry points.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_agent.domain.models import Match, Team

# canonical_key -> list of aliases (any language / shorthand)
TEAM_ALIASES: Dict[str, List[str]] = {
    "arsenal": ["arsenal", "arsenal fc", "арсенал"],
    "aston villa": ["aston villa", "aston", "астон вилла", "вилла"],
    "bournemouth": ["bournemouth", "afc bournemouth", "борнмут"],
    "brentford": ["brentford", "брентфорд"],
    "brighton": ["brighton", "brighton hove albion", "brighton and hove albion", "брайтон"],
    "chelsea": ["chelsea", "chelsea fc", "челси"],
    "crystal palace": ["crystal palace", "palace", "кристал пэлас", "кристал пелас"],
    "everton": ["everton", "эвертон"],
    "fulham": ["fulham", "фулхэм", "фулем"],
    "ipswich": ["ipswich", "ipswich town", "ипсвич"],
    "leicester": ["leicester", "leicester city", "лестер"],
    "liverpool": ["liverpool", "liverpool fc", "ливерпуль"],
    "manchester city": ["manchester city", "man city", "man. city", "манчестер сити", "ман сити", "сити"],
    "manchester united": ["manchester united", "man united", "man utd", "man. united", "манчестер юнайтед", "ман юнайтед", "юнайтед"],
    "newcastle": ["newcastle", "newcastle united", "ньюкасл"],
    "nottingham forest": ["nottingham forest", "nottingham", "ноттингем", "форест"],
    "southampton": ["southampton", "сантухэм", "сотон"],
    "tottenham hotspur": ["tottenham hotspur", "tottenham", "spurs", "тоттенхэм", "тоттенэм", "шпоры"],
    "west ham": ["west ham", "west ham united", "вест хэм", "вест-хэм"],
    "wolverhampton": ["wolverhampton", "wolves", "wolverhampton wanderers", "вулверхэмптон", "волки"],
    "atletico madrid": ["atletico madrid", "atletico", "atl madrid", "атлетико", "атлетико мадрид"],
    "barcelona": ["barcelona", "fc barcelona", "барселона", "барса"],
    "real madrid": ["real madrid", "real", "реал", "реал мадрид"],
    "bayern": ["bayern munich", "bayern", "fc bayern", "бавария", "байерн"],
    "borussia dortmund": ["borussia dortmund", "dortmund", "bvb", "боруссия", "дортмунд"],
    "inter": ["inter", "inter milan", "интер"],
    "juventus": ["juventus", "juve", "ювентус"],
    "milan": ["milan", "ac milan", "милан"],
    "napoli": ["napoli", "наполи"],
    "roma": ["roma", "as roma", "рома"],
    "psg": ["paris saint germain", "paris sg", "psg", "псж", "пари сен жермен"],
    "lyon": ["lyon", "olympique lyon", "лион"],
    "marseille": ["marseille", "olympique marseille", "марсель"],
    "monaco": ["monaco", "as monaco", "монако"],
    "lille": ["lille", "лилль"],
}

_STRIP_TOKENS = frozenset({"fc", "cf", "afc", "sc", "ac", "bk", "fk", "cd", "ud", "the", "de", "and"})


def normalize_team_name(name: str) -> str:
    """Lowercase, strip accents, remove club suffix noise and punctuation."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    tokens = [t for t in text.split() if t and t not in _STRIP_TOKENS]
    return " ".join(tokens)


def _alias_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for canonical, aliases in TEAM_ALIASES.items():
        for alias in aliases:
            index[normalize_team_name(alias)] = canonical
        index[normalize_team_name(canonical)] = canonical
    return index


_ALIAS_INDEX = _alias_index()


def canonical_team_key(name: str) -> str:
    norm = normalize_team_name(name)
    return _ALIAS_INDEX.get(norm, norm)


@dataclass(frozen=True)
class TeamMatchCandidate:
    team: Team
    score: float
    matched_via: str


def score_team_query(query: str, team: Team) -> float:
    """Score how well query matches API team (0..1)."""
    q = normalize_team_name(query)
    if not q:
        return 0.0

    names = [normalize_team_name(team.name)]
    if team.short_name:
        names.append(normalize_team_name(team.short_name))

    best = 0.0
    for api_norm in names:
        if not api_norm:
            continue
        if q == api_norm:
            return 1.0
        if q in api_norm or api_norm in q:
            best = max(best, 0.92)
        q_canon = canonical_team_key(q)
        api_canon = canonical_team_key(api_norm)
        if q_canon and q_canon == api_canon:
            best = max(best, 0.95)
        q_tokens = set(q.split())
        api_tokens = set(api_norm.split())
        if q_tokens and q_tokens <= api_tokens:
            best = max(best, 0.88)
        elif api_tokens and api_tokens <= q_tokens:
            best = max(best, 0.85)
        overlap = len(q_tokens & api_tokens) / max(len(q_tokens), 1)
        if overlap >= 0.5:
            best = max(best, 0.55 + 0.35 * overlap)

    return round(min(best, 1.0), 4)


def resolve_match_by_teams(
    home_query: str,
    away_query: str,
    matches: Sequence[Match],
    *,
    min_score: float = 0.72,
    ambiguity_gap: float = 0.06,
) -> Tuple[Optional[Match], Optional[str]]:
    """
    Find best match for home/away queries.
    Returns (match, None) or (None, user_message).
    """
    if not matches:
        return None, "На указанную дату матчей не найдено."

    scored: List[Tuple[Match, float, float]] = []
    for match in matches:
        sh = score_team_query(home_query, match.home_team)
        sa = score_team_query(away_query, match.away_team)
        combined = (sh + sa) / 2.0
        if sh >= 0.5 and sa >= 0.5:
            scored.append((match, combined, min(sh, sa)))

    if not scored:
        return None, (
            f"Матч не найден: {home_query} — {away_query}. "
            "Уточните названия команд (можно по-русски или кратко: Tottenham, Ман Сити)."
        )

    scored.sort(key=lambda x: x[1], reverse=True)
    best_match, best_score, _ = scored[0]
    if best_score < min_score:
        return None, (
            f"Матч не найден уверенно: {home_query} — {away_query} "
            f"(лучший score {best_score:.2f})."
        )

    if len(scored) > 1 and (best_score - scored[1][1]) < ambiguity_gap:
        lines = [
            f"• {m.home_team.name} — {m.away_team.name} (score {s:.2f})"
            for m, s, _ in scored[:3]
        ]
        return None, "Найдено несколько похожих матчей:\n" + "\n".join(lines)

    return best_match, None
