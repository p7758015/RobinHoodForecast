"""Match team-name resolution for v2 prediction results (OpenClaw / generic v2)."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from football_agent.domain.models import Team
from football_agent.domain.models_v2 import MatchPredictionResultV2, TeamRefV2
from football_agent.normalizers.team_name_resolver import score_team_query


def _ref_to_team(ref: TeamRefV2) -> Team:
    short = ref.short_name or ref.name
    return Team(id=ref.team_id, name=ref.name, short_name=short or ref.name)


def resolve_prediction_result_by_teams(
    home_query: str,
    away_query: str,
    results: Sequence[MatchPredictionResultV2],
    *,
    min_score: float = 0.72,
    ambiguity_gap: float = 0.06,
) -> Tuple[Optional[MatchPredictionResultV2], Optional[str]]:
    """
    Same spirit as ``resolve_match_by_teams`` but for already-scored v2 results
    (e.g. OpenClaw date batch).
    """
    if not results:
        return None, "На указанную дату матчей не найдено."

    scored: List[Tuple[MatchPredictionResultV2, float, float]] = []
    for r in results:
        sh = score_team_query(home_query, _ref_to_team(r.match_meta.home_team))
        sa = score_team_query(away_query, _ref_to_team(r.match_meta.away_team))
        combined = (sh + sa) / 2.0
        if sh >= 0.5 and sa >= 0.5:
            scored.append((r, combined, min(sh, sa)))

    if not scored:
        return None, (
            f"Матч не найден: {home_query} — {away_query}. "
            "Уточните названия команд (можно по-русски или кратко)."
        )

    scored.sort(key=lambda x: x[1], reverse=True)
    best_r, best_score, _ = scored[0]

    if best_score < min_score:
        return None, (
            f"Матч не найден уверенно: {home_query} — {away_query} "
            f"(лучший score {best_score:.2f})."
        )

    if len(scored) > 1 and (best_score - scored[1][1]) < ambiguity_gap:
        lines = [
            f"• {x.match_meta.home_team.name} — {x.match_meta.away_team.name} (score {s:.2f})"
            for x, s, _ in scored[:3]
        ]
        return None, "Найдено несколько похожих матчей:\n" + "\n".join(lines)

    return best_r, None
