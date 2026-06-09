"""Competition-aware context classification (conservative, ops-facing)."""

from __future__ import annotations

from enum import Enum


class CompetitionContextClass(str, Enum):
    """User/ops-facing competition bucket for guardrails."""

    LEAGUE = "league"
    DOMESTIC_CUP = "domestic_cup"
    INTERNATIONAL_CLUB = "international_club"
    NATIONAL_TEAM = "national_team"
    FRIENDLY = "friendly"
    UNKNOWN = "unknown"


COMPETITION_CONTEXT_LABELS_RU: dict[CompetitionContextClass, str] = {
    CompetitionContextClass.LEAGUE: "лига",
    CompetitionContextClass.DOMESTIC_CUP: "кубок",
    CompetitionContextClass.INTERNATIONAL_CLUB: "международный клубный",
    CompetitionContextClass.NATIONAL_TEAM: "сборные",
    CompetitionContextClass.FRIENDLY: "товарищеский",
    CompetitionContextClass.UNKNOWN: "неизвестный тип",
}
