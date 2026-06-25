"""Team ownership / alias resolution for Brave news signals (fail-soft)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Set

from football_agent.news_context.coach_normalize import fold_text

TeamSide = Literal["home", "away", "both", "unassigned"]

# Nicknames / press aliases for Brazilian clubs (extend per league as needed).
_BRAZIL_HOME_ALIASES: dict[str, tuple[str, ...]] = {
    "america_mg": ("america mineiro", "america-mg", "america mg", "coelho", "galo da massa"),
    "atletico_mg": ("atletico mineiro", "galo"),
    "cruzeiro": ("raposa",),
}
_BRAZIL_AWAY_ALIASES: dict[str, tuple[str, ...]] = {
    "criciuma": ("criciuma", "criciúma", "tigre", "criciuma ec"),
    "avai": ("avai", "leão da ilha"),
}

_COACH_NEAR_TEAM_RE = re.compile(
    r"(?:t[eé]cnico|treinador|comando)\s+(?:do|da|de)?\s*([^.!?\n]{0,60})",
    re.I,
)
_MINEIRO_RE = re.compile(r"\btime mineiro\b|\bmineir[oa]\b", re.I)
_CATARINENSE_RE = re.compile(r"\btime catarinense\b|\bcatarinense\b|\btigre\b", re.I)


@dataclass(frozen=True)
class TeamScope:
    home_team: str
    away_team: str
    home_aliases: frozenset[str]
    away_aliases: frozenset[str]

    def aliases_for(self, side: TeamSide) -> frozenset[str]:
        if side == "home":
            return self.home_aliases
        if side == "away":
            return self.away_aliases
        return frozenset()


@dataclass(frozen=True)
class ScopedOwnership:
    side: TeamSide
    confidence: float
    home_hits: int = 0
    away_hits: int = 0
    reason: str = ""


def _base_tokens(team: str) -> Set[str]:
    folded = fold_text(team)
    tokens: Set[str] = {folded}
    for part in folded.replace("-", " ").split():
        if len(part) >= 3:
            tokens.add(part)
    return tokens


def _lookup_extra_aliases(team: str, *, side: str) -> tuple[str, ...]:
    key = fold_text(team).replace(" ", "_").replace("-", "_")
    table = _BRAZIL_HOME_ALIASES if side == "home" else _BRAZIL_AWAY_ALIASES
    for alias_key, extras in table.items():
        if alias_key in key or key in alias_key:
            return extras
        if any(tok in alias_key for tok in _base_tokens(team)):
            return extras
    # Heuristic: America MG
    folded = fold_text(team)
    if side == "home" and "america" in folded:
        return _BRAZIL_HOME_ALIASES.get("america_mg", ())
    if side == "away" and "criciuma" in folded:
        return _BRAZIL_AWAY_ALIASES.get("criciuma", ())
    return ()


def build_team_scope(
    home_team: str,
    away_team: str,
    *,
    competition_country: Optional[str] = None,
) -> TeamScope:
    home_aliases: Set[str] = set(_base_tokens(home_team))
    away_aliases: Set[str] = set(_base_tokens(away_team))
    cc = (competition_country or "").strip().lower()
    if cc in ("brazil", "brasil") or not cc:
        home_aliases.update(_lookup_extra_aliases(home_team, side="home"))
        away_aliases.update(_lookup_extra_aliases(away_team, side="away"))
    return TeamScope(
        home_team=home_team,
        away_team=away_team,
        home_aliases=frozenset(home_aliases),
        away_aliases=frozenset(away_aliases),
    )


def _count_alias_hits(text: str, aliases: frozenset[str]) -> int:
    folded = fold_text(text)
    hits = 0
    for alias in aliases:
        if not alias:
            continue
        if alias in folded:
            hits += 1
    return hits


def classify_ownership(
    text: str,
    scope: TeamScope,
    *,
    home_coach: Optional[str] = None,
    away_coach: Optional[str] = None,
) -> ScopedOwnership:
    """Assign text to home/away/both/unassigned with confidence."""
    if not (text or "").strip():
        return ScopedOwnership("unassigned", 0.0, reason="empty")

    home_hits = _count_alias_hits(text, scope.home_aliases)
    away_hits = _count_alias_hits(text, scope.away_aliases)

    if _coach_mentioned(home_coach, text):
        home_hits += 3
    if _coach_mentioned(away_coach, text):
        away_hits += 3

    if _MINEIRO_RE.search(text) and "america" in fold_text(scope.home_team):
        home_hits += 2
    if _CATARINENSE_RE.search(text):
        away_hits += 1
    if "coelho" in fold_text(text) and "america" in fold_text(scope.home_team):
        home_hits += 2

    # Motivation phrases scoped by typical usage
    low = fold_text(text)
    if "lanterna" in low and home_hits == 0 and away_hits == 0:
        if "america" in low or "mineiro" in low or "coelho" in low:
            home_hits += 2
    if re.search(r"mira\s+g-?6", low) or "embalad" in low:
        if "criciuma" in low:
            away_hits += 2
        elif away_hits == 0 and home_hits == 0:
            away_hits += 1

    if home_hits == 0 and away_hits == 0:
        return ScopedOwnership("unassigned", 0.15, reason="no_team_alias")

    if home_hits > 0 and away_hits > 0:
        if home_hits >= away_hits * 2:
            conf = min(1.0, 0.45 + 0.15 * home_hits)
            return ScopedOwnership("home", conf, home_hits, away_hits, "home_dominant")
        if away_hits >= home_hits * 2:
            conf = min(1.0, 0.45 + 0.15 * away_hits)
            return ScopedOwnership("away", conf, home_hits, away_hits, "away_dominant")
        return ScopedOwnership("both", 0.35, home_hits, away_hits, "both_teams_mentioned")

    if home_hits > 0:
        return ScopedOwnership("home", min(1.0, 0.5 + 0.12 * home_hits), home_hits, away_hits, "home_only")
    return ScopedOwnership("away", min(1.0, 0.5 + 0.12 * away_hits), home_hits, away_hits, "away_only")


def _coach_mentioned(coach: Optional[str], text: str) -> bool:
    if not coach:
        return False
    folded_text = fold_text(text)
    folded_coach = fold_text(coach)
    if folded_coach and folded_coach in folded_text:
        return True
    parts = [p for p in folded_coach.split() if len(p) >= 4]
    return any(p in folded_text for p in parts)


def ownership_allows_side(ownership: ScopedOwnership, side: str, *, min_confidence: float = 0.35) -> bool:
    if ownership.side == "both":
        return ownership.confidence >= min_confidence
    if ownership.side == side:
        return ownership.confidence >= min_confidence
    if ownership.side == "unassigned":
        return False
    return False


def extract_coach_name_scoped(
    text: str,
    scope: TeamScope,
    *,
    side: str,
) -> tuple[Optional[str], float]:
    """Extract coach name only when sentence scope matches side."""
    from football_agent.news_context.coach_normalize import normalize_coach_name

    own = classify_ownership(text, scope)
    if not ownership_allows_side(own, side, min_confidence=0.35):
        return None, 0.0

    m = re.search(
        r"(?:coach|manager|head coach|t[eé]cnico|treinador)\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+){0,2})",
        text,
        re.I,
    )
    if not m:
        return None, 0.0

    name = normalize_coach_name(m.group(1).strip())
    if not name:
        return None, 0.0

    coach_near = _COACH_NEAR_TEAM_RE.search(text)
    if coach_near:
        fragment = fold_text(coach_near.group(1))
        aliases = scope.aliases_for(side)  # type: ignore[arg-type]
        if aliases and not any(a in fragment for a in aliases):
            if own.side != side:
                return None, 0.0
            return name, max(0.25, own.confidence * 0.7)

    return name, own.confidence


def split_signals_by_side(
    snippets: list[str],
    scope: TeamScope,
    *,
    home_coach: Optional[str] = None,
    away_coach: Optional[str] = None,
) -> tuple[list[str], list[str], list[str]]:
    """Return (home, away, unassigned) signal lists."""
    home: list[str] = []
    away: list[str] = []
    unassigned: list[str] = []
    seen: set[str] = set()
    for raw in snippets:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        own = classify_ownership(text, scope, home_coach=home_coach, away_coach=away_coach)
        if own.side == "home":
            home.append(text)
        elif own.side == "away":
            away.append(text)
        elif own.side == "both" and own.confidence >= 0.35:
            home.append(text)
            away.append(text)
        else:
            unassigned.append(text)
    return home, away, unassigned
