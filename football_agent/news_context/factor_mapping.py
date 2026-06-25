"""Map Brave general_news signals into snapshot factor hints (heuristic, fail-soft)."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from football_agent.domain.enums_v2 import MotivationContext
from football_agent.domain.models_v2 import TeamMotivationBlockV2
from football_agent.news_context.coach_normalize import fold_text
from football_agent.news_context.models import GeneralNewsBlock, MatchNewsContext
from football_agent.news_context.team_scope import build_team_scope, classify_ownership

_MOTIVATION_HOME_RELEGATION_RE = re.compile(
    r"\blanterna\b|\bzona de rebaixamento\b|\bz4\b|\bperman[eê]ncia\b|\bescapar\b.*\brebaix",
    re.I,
)
_MOTIVATION_AWAY_TOP_RE = re.compile(
    r"\bmira\s+g-?6\b|\bbrigando\b.*\bacesso\b|\bg-?4\b|\btop\s+\d\b|\bembalad[oa]\b",
    re.I,
)
_MOTIVATION_FIGHT_RE = re.compile(
    r"\bmust[- ]win\b|\btr[eê]s pontos\b|\bdecisiv[oa]\b|\bpress[aã]o\b",
    re.I,
)

_PLAYER_GOALKEEPER_RE = re.compile(
    r"goleir[oa]\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)?)",
    re.I,
)
_PLAYER_SUSPENDED_RE = re.compile(
    r"(?:o|a)\s+([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)?).{0,80}(?:suspenso|suspens|cart[aã]o amarelo)",
    re.I,
)
_PLAYER_OUT_RE = re.compile(
    r"(?:desfalque|fora|lesionad[oa]|contundid[oa]).{0,40}(?:o|a)\s+([A-ZÀ-Ú][a-zà-ú]+)",
    re.I,
)


def extract_player_hint_from_signal(text: str) -> Tuple[Optional[str], Optional[str]]:
    for pat, role in (
        (_PLAYER_GOALKEEPER_RE, "goalkeeper"),
        (_PLAYER_SUSPENDED_RE, "unknown"),
        (_PLAYER_OUT_RE, "unknown"),
    ):
        m = pat.search(text)
        if m:
            name = m.group(1).strip()
            if len(name) >= 3:
                return name, role
    return None, None


def _side_signal_list(gn: GeneralNewsBlock, side: str, kind: str) -> List[str]:
    if side == "home":
        if kind == "injury":
            return list(gn.home_injuries_signals or [])
        if kind == "suspension":
            return list(gn.home_suspension_signals or [])
        return list(gn.home_motivation_signals or [])
    if kind == "injury":
        return list(gn.away_injuries_signals or [])
    if kind == "suspension":
        return list(gn.away_suspension_signals or [])
    return list(gn.away_motivation_signals or [])


def apply_brave_motivation_bias(
    block: TeamMotivationBlockV2,
    news: Optional[MatchNewsContext],
    *,
    side: str,
    home_team: str,
    away_team: str,
) -> TeamMotivationBlockV2:
    """Soft motivation_score/context nudge from side-scoped Brave preview language."""
    if news is None or not (news.source_count or 0) or news.general_news is None:
        return block

    gn = news.general_news
    blob = " ".join(_side_signal_list(gn, side, "motivation"))
    if not blob.strip():
        return block

    score = block.motivation_score
    ctx = block.motivation_context
    is_home = side == "home"
    scope = build_team_scope(home_team, away_team)

    for sentence in re.split(r"[.!?\n]+", blob):
        text = sentence.strip()
        if not text:
            continue
        own = classify_ownership(text, scope)
        if own.side not in (side, "both") or own.confidence < 0.35:
            continue
        if is_home and _MOTIVATION_HOME_RELEGATION_RE.search(text):
            score = min(1.0, score + 0.12)
            if ctx in (None, MotivationContext.MIDTABLE_NEUTRAL):
                ctx = MotivationContext.RELEGATION_BATTLE
        if not is_home and _MOTIVATION_AWAY_TOP_RE.search(text):
            score = min(1.0, score + 0.1)
            if ctx in (None, MotivationContext.MIDTABLE_NEUTRAL):
                ctx = MotivationContext.EURO_RACE
        if _MOTIVATION_FIGHT_RE.search(text):
            score = min(1.0, score + 0.06)

    return block.model_copy(update={"motivation_score": score, "motivation_context": ctx})
