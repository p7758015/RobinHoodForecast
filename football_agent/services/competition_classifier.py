"""
Conservative competition context classification from Flashscore/meta signals.

Prefer ``unknown`` over false precision. No network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta

# Explicit non-league tournament types from ingest — trusted when set intentionally.
_EXPLICIT_TOURNAMENT_MAP: dict[TournamentType, CompetitionContextClass] = {
    TournamentType.LEAGUE_REGULAR: CompetitionContextClass.LEAGUE,
    TournamentType.DOMESTIC_CUP: CompetitionContextClass.DOMESTIC_CUP,
    TournamentType.INTERNATIONAL_CLUB: CompetitionContextClass.INTERNATIONAL_CLUB,
    TournamentType.INTERNATIONAL_NATIONAL: CompetitionContextClass.NATIONAL_TEAM,
    TournamentType.FRIENDLY: CompetitionContextClass.FRIENDLY,
}

_CONTEXT_TO_TOURNAMENT: dict[CompetitionContextClass, TournamentType] = {
    CompetitionContextClass.LEAGUE: TournamentType.LEAGUE_REGULAR,
    CompetitionContextClass.DOMESTIC_CUP: TournamentType.DOMESTIC_CUP,
    CompetitionContextClass.INTERNATIONAL_CLUB: TournamentType.INTERNATIONAL_CLUB,
    CompetitionContextClass.NATIONAL_TEAM: TournamentType.INTERNATIONAL_NATIONAL,
    CompetitionContextClass.FRIENDLY: TournamentType.FRIENDLY,
    CompetitionContextClass.UNKNOWN: TournamentType.LEAGUE_REGULAR,
}

_FRIENDLY_RE = re.compile(
    r"\b(friendly|friendlies|club\s+friendly|international\s+friendly|товарищеск)\b",
    re.I,
)
_NATIONAL_RE = re.compile(
    r"\b(world\s+cup|euro\s*\d|european\s+championship|nations\s+league|"
    r"qualification|qualifying|international\s+match|fifa|uefa\s+nations|"
    r"afc\s+asian\s+cup|copa\s+america|gold\s+cup|afcon|euro\s+qual)\b",
    re.I,
)
_INTL_CLUB_RE = re.compile(
    r"\b(champions\s+league|europa\s+league|conference\s+league|uefa\s+super\s+cup|"
    r"copa\s+libertadores|copa\s+sudamericana|afc\s+champions|club\s+world\s+cup|"
    r"intertoto|recopa)\b",
    re.I,
)
_DOMESTIC_CUP_RE = re.compile(
    r"\b(fa\s+cup|coppa\s+italia|dfb[\s-]?pokal|coupe\s+de\s+france|"
    r"domestic\s+cup|national\s+cup|kupa|pokal|copa\s+del\s+rey|"
    r"league\s+cup|carabao\s+cup|efl\s+cup|trophy|super\s+cup)\b",
    re.I,
)
_LEAGUE_RE = re.compile(
    r"\b(premier\s+league|serie\s+a|bundesliga|ligue\s+1|la\s?liga|"
    r"botola|eredivisie|primeira\s+liga|super\s+lig|championship|"
    r"division\s+\d|regular\s+season|league\s+one|league\s+two)\b",
    re.I,
)

_NATIONAL_TEAM_NAMES = frozenset(
    {
        "brazil",
        "argentina",
        "france",
        "germany",
        "spain",
        "italy",
        "england",
        "portugal",
        "netherlands",
        "belgium",
        "croatia",
        "morocco",
        "senegal",
        "usa",
        "mexico",
        "japan",
        "south korea",
        "korea republic",
    },
)


@dataclass(frozen=True)
class CompetitionClassification:
    category: CompetitionContextClass
    tournament_type: TournamentType
    confidence: str  # high | low
    signals: List[str] = field(default_factory=list)
    source: str = "heuristic"  # explicit_tournament_type | competition_name | team_names | default

    @property
    def is_league(self) -> bool:
        return self.category == CompetitionContextClass.LEAGUE

    @property
    def requires_guardrail(self) -> bool:
        return self.category != CompetitionContextClass.LEAGUE

    def to_debug_dict(self) -> dict:
        return {
            "category": self.category.value,
            "tournament_type": self.tournament_type.value,
            "confidence": self.confidence,
            "signals": list(self.signals),
            "source": self.source,
        }


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def _looks_like_national_teams(home: str, away: str) -> bool:
    h = _norm(home)
    a = _norm(away)
    if h in _NATIONAL_TEAM_NAMES and a in _NATIONAL_TEAM_NAMES:
        return True
    if re.search(r"\b(national team|nt)\b", h) or re.search(r"\b(national team|nt)\b", a):
        return True
    return False


def classify_competition_meta(meta: FlashscoreMeta) -> CompetitionClassification:
    """Classify from Flashscore meta fields only."""
    signals: List[str] = []
    explicit = meta.tournament_type

    if explicit and explicit != TournamentType.LEAGUE_REGULAR:
        cat = _EXPLICIT_TOURNAMENT_MAP.get(explicit, CompetitionContextClass.UNKNOWN)
        return CompetitionClassification(
            category=cat,
            tournament_type=explicit,
            confidence="high",
            signals=[f"explicit_tournament_type={explicit.value}"],
            source="explicit_tournament_type",
        )

    comp = _norm(meta.competition_name)
    stage = _norm(meta.stage)
    blob = " ".join(x for x in (comp, stage, _norm(meta.round)) if x)

    if _FRIENDLY_RE.search(blob):
        signals.append("competition_name:friendly")
        return _result(CompetitionContextClass.FRIENDLY, "high", signals, "competition_name")

    if _NATIONAL_RE.search(blob):
        signals.append("competition_name:national")
        return _result(CompetitionContextClass.NATIONAL_TEAM, "high", signals, "competition_name")

    if _INTL_CLUB_RE.search(blob):
        signals.append("competition_name:international_club")
        return _result(CompetitionContextClass.INTERNATIONAL_CLUB, "high", signals, "competition_name")

    if _DOMESTIC_CUP_RE.search(blob):
        signals.append("competition_name:domestic_cup")
        return _result(CompetitionContextClass.DOMESTIC_CUP, "high", signals, "competition_name")

    if _LEAGUE_RE.search(blob):
        signals.append("competition_name:league")
        return _result(CompetitionContextClass.LEAGUE, "high", signals, "competition_name")

    if _looks_like_national_teams(meta.home_team_name, meta.away_team_name):
        signals.append("team_names:national_teams")
        return _result(
            CompetitionContextClass.NATIONAL_TEAM,
            "low",
            signals,
            "team_names",
        )

    if comp and comp not in ("unknown competition", "unknown"):
        signals.append("competition_name:unmatched")
        return _result(CompetitionContextClass.UNKNOWN, "low", signals, "competition_name")

    return _result(CompetitionContextClass.UNKNOWN, "low", ["no_classification_signals"], "default")


def classify_competition_from_facts(facts: FlashscoreMatchFacts) -> CompetitionClassification:
    return classify_competition_meta(facts.meta)


def refine_meta_tournament_type(meta: FlashscoreMeta) -> FlashscoreMeta:
    """Return meta copy with tournament_type aligned to conservative classification."""
    clf = classify_competition_meta(meta)
    if meta.tournament_type != TournamentType.LEAGUE_REGULAR:
        return meta
    if clf.category == CompetitionContextClass.UNKNOWN:
        return meta
    return meta.model_copy(update={"tournament_type": clf.tournament_type})


def _result(
    category: CompetitionContextClass,
    confidence: str,
    signals: List[str],
    source: str,
) -> CompetitionClassification:
    tt = _CONTEXT_TO_TOURNAMENT.get(category, TournamentType.LEAGUE_REGULAR)
    conf = "high" if confidence == "high" else "low"
    return CompetitionClassification(
        category=category,
        tournament_type=tt,
        confidence=conf,
        signals=signals,
        source=source,
    )
