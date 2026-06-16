"""
Conservative competition context classification from Flashscore/meta signals.

Prefer ``unknown`` over false precision. No network calls.

Rule priority (after explicit ``tournament_type`` from ingest):
  friendly → international club → national team → domestic cup → league → unknown
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta
from football_agent.league_registry import registry_league_display_names_lower

# Explicit non-league tournament types from ingest — trusted when set intentionally.
_EXPLICIT_TOURNAMENT_MAP: dict[TournamentType, CompetitionContextClass] = {
    TournamentType.LEAGUE_REGULAR: CompetitionContextClass.LEAGUE,
    TournamentType.DOMESTIC_CUP: CompetitionContextClass.DOMESTIC_CUP,
    TournamentType.INTERNATIONAL_CLUB: CompetitionContextClass.INTERNATIONAL_CLUB,
    TournamentType.INTERNATIONAL_NATIONAL: CompetitionContextClass.NATIONAL_TEAM,
    TournamentType.FRIENDLY: CompetitionContextClass.FRIENDLY,
    TournamentType.UNKNOWN: CompetitionContextClass.UNKNOWN,
}

_CONTEXT_TO_TOURNAMENT: dict[CompetitionContextClass, TournamentType] = {
    CompetitionContextClass.LEAGUE: TournamentType.LEAGUE_REGULAR,
    CompetitionContextClass.DOMESTIC_CUP: TournamentType.DOMESTIC_CUP,
    CompetitionContextClass.INTERNATIONAL_CLUB: TournamentType.INTERNATIONAL_CLUB,
    CompetitionContextClass.NATIONAL_TEAM: TournamentType.INTERNATIONAL_NATIONAL,
    CompetitionContextClass.FRIENDLY: TournamentType.FRIENDLY,
    CompetitionContextClass.UNKNOWN: TournamentType.UNKNOWN,
}

_FRIENDLY_RE = re.compile(
    r"\b("
    r"friendly|friendlies|club\s+friendly|international\s+friendly|товарищеск|"
    r"pre[- ]?season|preseason|"
    r"audi\s+cup|florida\s+cup|emirates\s+cup|international\s+champions\s+cup"
    r")\b",
    re.I,
)

_INTL_CLUB_RE = re.compile(
    r"\b("
    r"(?:uefa\s+)?champions\s+league|(?:uefa\s+)?europa\s+league|(?:uefa\s+)?conference\s+league|"
    r"uefa\s+super\s+cup|"
    r"copa\s+libertadores|copa\s+sudamericana|recopa\s+sudamericana|"
    r"afc\s+champions(?:\s+league)?|concacaf\s+champions(?:\s+(?:cup|league))?|"
    r"(?:fifa\s+)?club\s+world\s+cup|leagues\s+cup"
    r")\b",
    re.I,
)

_INTL_CLUB_QUAL_RE = re.compile(
    r"("
    r"(?:qualif\w*|preliminary|play[- ]?offs?)\b.*\b("
    r"champions\s+league|europa\s+league|conference\s+league|copa\s+libertadores|"
    r"copa\s+sudamericana|afc\s+champions|concacaf\s+champions|leagues\s+cup"
    r")\b|"
    r"\b(champions\s+league|europa\s+league|conference\s+league|copa\s+libertadores|"
    r"afc\s+champions|concacaf\s+champions)\b.*\b(qualif\w*|preliminary|play[- ]?offs?)"
    r")",
    re.I,
)

_NATIONAL_RE = re.compile(
    r"\b("
    r"fifa\s+world\s+cup|world\s+cup|"
    r"european\s+championship|uefa\s+euro|\beuro\s+20\d{2}\b|euro\s+qualif\w*|"
    r"world\s+cup\s+qualif\w*|fifa\s+world\s+cup\s+qualif\w*|"
    r"uefa\s+nations\s+league|concacaf\s+nations\s+league|"
    r"copa\s+america|"
    r"africa\s+cup\s+of\s+nations|afcon|afcon\s+qualif\w*|"
    r"(?:afc\s+)?asian\s+cup|asian\s+cup\s+qualif\w*|"
    r"gold\s+cup|"
    r"olympic\s+(?:football|soccer|games)|"
    r"international\s+friendly"
    r")\b",
    re.I,
)

_DOMESTIC_CUP_RE = re.compile(
    r"\b("
    r"fa\s+cup|efl\s+cup|carabao\s+cup|league\s+cup|"
    r"copa\s+del\s+rey|coppa\s+italia|dfb[\s-]?pokal|coupe\s+de\s+france|"
    r"ta[cç]a\s+de\s+portugal|knvb\s+beker|"
    r"supercoppa\s+italiana|supercopa\s+de\s+espa[nñ]a|community\s+shield|"
    r"supercoppa|supercopa(?!\s+de\s+europa)|"
    r"domestic\s+cup|national\s+cup"
    r")\b",
    re.I,
)

# Domestic super cups (UEFA Super Cup handled by intl club, checked earlier).
_DOMESTIC_SUPER_CUP_RE = re.compile(
    r"\b(super\s+cup|supercup)\b",
    re.I,
)

_LEAGUE_RE = re.compile(
    r"\b("
    r"premier\s+league|la\s?liga|primera\s+divisi[oó]n|serie\s+a|bundesliga|ligue\s+1|"
    r"eredivisie|primeira\s+liga|liga\s+portugal|scottish\s+premiership|"
    r"jupiler\s+pro\s+league|belgian\s+pro\s+league|super\s+lig|"
    r"liga\s+mx|major\s+league\s+soccer|\bmls\b|"
    r"brasileir[aã]o|campeonato\s+brasileiro|brazil\s+serie\s+[ab]|serie\s+b|"
    r"liga\s+profesional|primera\s+divisi[oó]n\s+argentina|"
    r"saudi\s+pro\s+league|saudi\s+professional\s+league|"
    r"j1\s+league|j[\s-]?league|k\s+league\s+1|k[\s-]?league|"
    r"super\s+league\s+greece|greek\s+super\s+league|"
    r"swiss\s+super\s+league|austrian\s+bundesliga|"
    r"czech\s+first\s+league|fortuna\s+liga|danish\s+superliga|superliga|"
    r"eliteserien|allsvenskan|ekstraklasa|liga\s+i\s+romania|romanian\s+superliga|"
    r"hnl|croatian\s+football\s+league|serbian\s+super\s+liga|"
    r"ukrainian\s+premier\s+league|premier\s+liga|"
    r"botola|egyptian\s+premier\s+league|"
    r"kazakhstan\s+premier|qazaqstan\s+premier|"
    r"meistriliiga|premium\s+liiga|virsl[iī]ga|"
    r"league\s+one|league\s+two|regular\s+season|division\s+\d+"
    r")\b",
    re.I,
)

_CHAMPIONSHIP_LEAGUE_RE = re.compile(r"\bchampionship\b", re.I)
_CHAMPIONS_LEAGUE_GUARD = re.compile(r"champions\s+league", re.I)

_LEAGUE_SUBSTRINGS: Tuple[str, ...] = (
    "premier league",
    "la liga",
    "laliga",
    "primera division",
    "serie a",
    "bundesliga",
    "ligue 1",
    "eredivisie",
    "primeira liga",
    "scottish premiership",
    "jupiler pro league",
    "pro league",
    "super lig",
    "liga mx",
    "major league soccer",
    "brasileirao",
    "campeonato brasileiro",
    "saudi pro league",
    "j1 league",
    "k league 1",
    "super league greece",
    "swiss super league",
    "austrian bundesliga",
    "czech first league",
    "fortuna liga",
    "superliga",
    "eliteserien",
    "allsvenskan",
    "ekstraklasa",
    "romanian superliga",
    "hnl",
    "serbian super liga",
    "ukrainian premier league",
    "botola",
    "egyptian premier league",
    "kazakhstan premier",
    "meistriliiga",
    "premium liiga",
    "virsliga",
    "league one",
    "league two",
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
        "united states",
        "mexico",
        "japan",
        "south korea",
        "korea republic",
        "uruguay",
        "colombia",
        "chile",
        "poland",
        "ukraine",
        "turkey",
        "wales",
        "scotland",
        "serbia",
        "switzerland",
        "austria",
        "denmark",
        "sweden",
        "norway",
        "egypt",
        "nigeria",
        "cameroon",
        "ghana",
        "ivory coast",
        "cote d'ivoire",
        "saudi arabia",
        "iran",
        "qatar",
        "australia",
        "canada",
        "costa rica",
        "ecuador",
        "peru",
        "paraguay",
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
    def is_league_eligible(self) -> bool:
        """Routing gate: only high-confidence league classifications."""
        return self.category == CompetitionContextClass.LEAGUE and self.confidence == "high"

    @property
    def requires_guardrail(self) -> bool:
        return self.category != CompetitionContextClass.LEAGUE

    def to_debug_dict(self) -> dict:
        return {
            "category": self.category.value,
            "tournament_type": self.tournament_type.value,
            "confidence": self.confidence,
            "is_league_eligible": self.is_league_eligible,
            "signals": list(self.signals),
            "source": self.source,
        }


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def _build_blob(meta: FlashscoreMeta) -> Tuple[str, str, str, str]:
    comp = _norm(meta.competition_name)
    stage = _norm(meta.stage)
    country = _norm(meta.competition_country)
    rnd = _norm(meta.round)
    blob = " ".join(x for x in (comp, stage, rnd, country) if x)
    return comp, stage, country, blob


def _looks_like_national_teams(home: str, away: str, *, comp: str) -> bool:
    h = _norm(home)
    a = _norm(away)
    if h in _NATIONAL_TEAM_NAMES and a in _NATIONAL_TEAM_NAMES:
        return True
    if re.search(r"\b(national team|u23|u21)\b", h) or re.search(r"\b(national team|u23|u21)\b", a):
        # Youth sides only when competition also hints international
        if re.search(r"\b(u21|u23|youth|international)\b", comp):
            return True
        if re.search(r"\b(u21|u23)\b", h) or re.search(r"\b(u21|u23)\b", a):
            return True
    return False


def _registry_league_match(comp: str) -> bool:
    if not comp:
        return False
    registry_names = registry_league_display_names_lower()
    if comp in registry_names:
        return True
    return any(name in comp or comp in name for name in registry_names if len(name) >= 4)


def _league_substring_match(comp: str) -> bool:
    if not comp:
        return False
    return any(sub in comp for sub in _LEAGUE_SUBSTRINGS)


def _is_english_championship_league(comp: str, blob: str, country: str) -> bool:
    if _CHAMPIONS_LEAGUE_GUARD.search(blob):
        return False
    if not _CHAMPIONSHIP_LEAGUE_RE.search(comp):
        return False
    if country in ("england", "united kingdom", "uk", "great britain"):
        return True
    return comp.strip() in ("championship", "efl championship", "english championship")


def _classify_league(comp: str, blob: str, country: str, stage: str) -> bool:
    if _registry_league_match(comp):
        return True
    if _league_substring_match(comp):
        return True
    if _is_english_championship_league(comp, blob, country):
        return True
    if _LEAGUE_RE.search(blob):
        return True
    if "regular season" in stage or "regular season" in blob:
        # Stage hint alone is weak; require a league-ish competition name
        if comp and comp not in ("unknown competition", "unknown"):
            return True
    return False


def classify_competition_meta(meta: FlashscoreMeta) -> CompetitionClassification:
    """Classify from Flashscore meta fields only."""
    signals: List[str] = []
    explicit = meta.tournament_type

    if explicit and explicit not in (TournamentType.LEAGUE_REGULAR, TournamentType.UNKNOWN):
        cat = _EXPLICIT_TOURNAMENT_MAP.get(explicit, CompetitionContextClass.UNKNOWN)
        return CompetitionClassification(
            category=cat,
            tournament_type=explicit,
            confidence="high",
            signals=[f"explicit_tournament_type={explicit.value}"],
            source="explicit_tournament_type",
        )

    comp, stage, country, blob = _build_blob(meta)

    if _FRIENDLY_RE.search(blob):
        signals.append("competition_name:friendly")
        return _result(CompetitionContextClass.FRIENDLY, "high", signals, "competition_name")

    if _INTL_CLUB_RE.search(blob) or _INTL_CLUB_QUAL_RE.search(blob):
        signals.append("competition_name:international_club")
        if "qualif" in blob or "preliminary" in blob or "play-off" in blob or "playoff" in blob:
            signals.append("international_club:qualifying")
        return _result(CompetitionContextClass.INTERNATIONAL_CLUB, "high", signals, "competition_name")

    if _NATIONAL_RE.search(blob):
        signals.append("competition_name:national")
        return _result(CompetitionContextClass.NATIONAL_TEAM, "high", signals, "competition_name")

    if _DOMESTIC_CUP_RE.search(blob) or (
        _DOMESTIC_SUPER_CUP_RE.search(blob) and not _INTL_CLUB_RE.search(blob)
    ):
        signals.append("competition_name:domestic_cup")
        return _result(CompetitionContextClass.DOMESTIC_CUP, "high", signals, "competition_name")

    if _classify_league(comp, blob, country, stage):
        signals.append("competition_name:league")
        if _registry_league_match(comp):
            signals.append("league:registry_hint")
        if country:
            signals.append(f"country:{country}")
        if "regular season" in stage:
            signals.append("stage:regular_season")
        return _result(CompetitionContextClass.LEAGUE, "high", signals, "competition_name")

    if _looks_like_national_teams(meta.home_team_name, meta.away_team_name, comp=comp):
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
    """
    Align ``meta.tournament_type`` with high-confidence classification.

    Unknown/low-confidence stays explicit via ``TournamentType.UNKNOWN`` so routing
    does not silently treat ambiguous competitions as league.
    """
    clf = classify_competition_meta(meta)
    if clf.source == "explicit_tournament_type":
        return meta
    if clf.confidence == "high":
        if meta.tournament_type == clf.tournament_type:
            return meta
        return meta.model_copy(update={"tournament_type": clf.tournament_type})
    if clf.category == CompetitionContextClass.UNKNOWN:
        if meta.tournament_type == TournamentType.UNKNOWN:
            return meta
        return meta.model_copy(update={"tournament_type": TournamentType.UNKNOWN})
    return meta


def _result(
    category: CompetitionContextClass,
    confidence: str,
    signals: List[str],
    source: str,
) -> CompetitionClassification:
    tt = _CONTEXT_TO_TOURNAMENT.get(category, TournamentType.UNKNOWN)
    conf = "high" if confidence == "high" else "low"
    return CompetitionClassification(
        category=category,
        tournament_type=tt,
        confidence=conf,
        signals=signals,
        source=source,
    )
