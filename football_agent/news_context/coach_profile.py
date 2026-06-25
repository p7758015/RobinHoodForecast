"""Long-term coach profile extraction from Brave (fail-soft, separate from match news)."""

from __future__ import annotations

import re
from typing import List, Optional

from football_agent.news_context.coach_normalize import normalize_coach_name
from football_agent.news_context.extraction import hits_to_sources
from football_agent.news_context.models import CoachProfileContextBlock
from football_agent.news_context.query_builder import build_coach_profile_queries
from football_agent.services.brave_search_client import BraveSearchHit

_TEAM_RE = re.compile(
    r"(?:at|no|na|em|pelo|pela|com)\s+([A-ZÀ-Ú][A-Za-zÀ-ú][A-Za-zÀ-ú\-\s]{2,35}?)(?:\s|,|\.|$)",
    re.I,
)
_TEAM_STOPWORDS = frozenset(
    {"the", "bagagem", "pelo", "pela", "futebol", "football", "clube", "time", "equipe", "seu", "sua"},
)
_TROPHY_RE = re.compile(
    r"\b(t[ií]tulo|campe[aã]o|champion|trophy|trof[eé]u|promo[cç][aã]o|acesso|s[eé]rie\s+[ab])\b",
    re.I,
)
_EXPERIENCE_SENIOR_RE = re.compile(
    r"\b(veteran|experienced|experi[eê]ncia|carreira longa|mais de \d+ anos)\b",
    re.I,
)
_EXPERIENCE_JUNIOR_RE = re.compile(
    r"\b(debut|primeiro cargo|estreia como t[eé]cnico|inexperienced)\b",
    re.I,
)


def extract_coach_profile_block(
    *,
    coach_name: str,
    hits: List[BraveSearchHit],
) -> CoachProfileContextBlock:
    blob = "\n".join(f"{h.title} {h.description or ''}" for h in hits)
    teams: List[str] = []
    achievements: List[str] = []
    seasons: List[str] = []

    for m in _TEAM_RE.finditer(blob):
        team = m.group(1).strip(" .,-")
        if team and team.lower() not in {coach_name.lower(), "futebol", "football"}:
            if team.lower() in _TEAM_STOPWORDS:
                continue
            if team not in teams and len(team) >= 3:
                teams.append(team)
        if len(teams) >= 6:
            break

    for m in _TROPHY_RE.finditer(blob):
        snippet = blob[max(0, m.start() - 40) : min(len(blob), m.end() + 80)].strip()
        if snippet and snippet not in achievements:
            achievements.append(snippet)
        if len(achievements) >= 4:
            break

    exp_level: Optional[str] = None
    if _EXPERIENCE_SENIOR_RE.search(blob):
        exp_level = "experienced"
    elif _EXPERIENCE_JUNIOR_RE.search(blob):
        exp_level = "emerging"
    elif len(teams) >= 4:
        exp_level = "experienced"
    elif teams:
        exp_level = "developing"

    strength = 0.5
    if achievements:
        strength += min(0.2, 0.05 * len(achievements))
    if len(teams) >= 3:
        strength += 0.08
    if exp_level == "experienced":
        strength += 0.06
    strength = max(0.0, min(1.0, strength))

    conf = 0.0
    if hits:
        conf = 0.25
    if teams:
        conf += 0.15
    if achievements:
        conf += 0.2
    conf = min(1.0, conf)

    summary = None
    if hits:
        summary = (hits[0].description or hits[0].title or "")[:240] or None

    missing: List[str] = []
    if not teams:
        missing.append("previous_teams")
    if not achievements:
        missing.append("major_achievements")

    return CoachProfileContextBlock(
        coach_name=normalize_coach_name(coach_name),
        previous_teams=teams[:6],
        major_achievements=achievements[:4],
        notable_seasons=seasons,
        estimated_experience_level=exp_level,
        coach_global_strength_score=strength,
        career_summary=summary,
        profile_confidence=conf,
        profile_sources=hits_to_sources(hits[:5]),
        missing_fields=missing,
        warnings=[] if hits else ["coach_profile_no_results"],
    )
