"""Resolve FlashscoreMatchFacts from team queries (shared by pipeline + collector)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from football_agent.domain.models import Team
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.normalizers.team_name_resolver import score_team_query


def pick_facts_by_teams(
    facts_list: List[FlashscoreMatchFacts],
    home_query: str,
    away_query: str,
    *,
    min_score: float = 0.72,
) -> Tuple[Optional[FlashscoreMatchFacts], Optional[str]]:
    if not facts_list:
        return None, "На указанную дату матчей не найдено."

    scored: List[Tuple[FlashscoreMatchFacts, float]] = []
    for facts in facts_list:
        home_team = Team(id=0, name=facts.meta.home_team_name, short_name=facts.meta.home_team_name)
        away_team = Team(id=0, name=facts.meta.away_team_name, short_name=facts.meta.away_team_name)
        sh = score_team_query(home_query, home_team)
        sa = score_team_query(away_query, away_team)
        combined = (sh + sa) / 2.0
        if sh >= 0.5 and sa >= 0.5:
            scored.append((facts, combined))

    if not scored:
        return None, f"Матч не найден: {home_query} — {away_query}."

    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    if best_score < min_score:
        return None, (
            f"Матч не найден уверенно: {home_query} — {away_query} "
            f"(score {best_score:.2f})."
        )
    return best, None
