"""Brave search query templates for match news / coach enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set


@dataclass(frozen=True)
class NewsSearchQuery:
    query: str
    category: str  # preview | injuries | coach | h2h | rotation


def build_match_news_queries(
    *,
    home_team: str,
    away_team: str,
    home_coach_name: Optional[str] = None,
    away_coach_name: Optional[str] = None,
    competition_name: Optional[str] = None,
    competition_country: Optional[str] = None,
    include_coach_terms: bool = True,
    include_injury_terms: bool = True,
    include_lineup_terms: bool = True,
) -> List[NewsSearchQuery]:
    """Build deduplicated query list (team-first; coach-specific when names known)."""
    home = (home_team or "").strip()
    away = (away_team or "").strip()
    if not home or not away:
        return []

    seen: Set[str] = set()
    out: List[NewsSearchQuery] = []

    def add(q: str, category: str) -> None:
        norm = " ".join(q.lower().split())
        if not norm or norm in seen:
            return
        seen.add(norm)
        out.append(NewsSearchQuery(query=q.strip(), category=category))

    add(f"{home} {away} match preview", "preview")
    add(f"{home} vs {away} preview", "preview")

    comp = (competition_name or "").strip()
    country = (competition_country or "").strip().lower()
    if comp:
        add(f"{home} {away} {comp} preview", "preview")
    if country == "brazil" or "serie b" in comp.lower():
        add(f"{home} x {away} Série B", "preview")
        add(f"palpite {home} {away} Série B", "preview")
        add(f"{home} {away} desfalques escalação", "injuries")
        add(f"{home} {away} desfalques suspenso", "injuries")
        add(f"desfalques {home} x {away}", "injuries")
        add(f"{home} escalação desfalques", "injuries")
        add(f"{away} escalação desfalques", "injuries")
        add(f"{home} técnico entrevista", "coach")
        add(f"{away} técnico entrevista", "coach")

    if include_injury_terms:
        add(f"{home} injuries lineup", "injuries")
        add(f"{away} injuries lineup", "injuries")
        add(f"{home} injury news", "injuries")
        add(f"{away} injury news", "injuries")

    if include_lineup_terms:
        add(f"{home} predicted lineup", "lineup")
        add(f"{away} predicted lineup", "lineup")

    add(f"{home} rotation squad", "rotation")
    add(f"{away} rotation squad", "rotation")

    if include_coach_terms:
        hc = (home_coach_name or "").strip()
        ac = (away_coach_name or "").strip()
        if hc:
            add(f"{hc} press conference", "coach")
            add(f"{hc} {home} coach", "coach")
        if ac:
            add(f"{ac} press conference", "coach")
            add(f"{ac} {away} coach", "coach")
        if hc and ac:
            add(f"{hc} {ac}", "h2h")
            add(f"{hc} vs {ac}", "h2h")
            add(f"{hc} against {ac} head to head", "h2h")

    return out


def build_coach_profile_queries(
    coach_name: str,
    *,
    competition_country: Optional[str] = None,
) -> List[NewsSearchQuery]:
    """Long-term coach profile queries (career/trophies) — not match news."""
    name = (coach_name or "").strip()
    if not name:
        return []
    seen: set[str] = set()
    out: List[NewsSearchQuery] = []
    country = (competition_country or "").strip().lower()

    def add(q: str) -> None:
        norm = " ".join(q.lower().split())
        if norm in seen:
            return
        seen.add(norm)
        out.append(NewsSearchQuery(query=q.strip(), category="coach_profile"))

    add(f"{name} football coach career teams")
    add(f"{name} treinador títulos carreira")
    add(f"{name} biography football coach")
    if country in ("brazil", "brasil"):
        add(f"{name} treinador clubes promoção acesso")
        add(f"{name} técnico títulos conquistados")
    return out[:4]
