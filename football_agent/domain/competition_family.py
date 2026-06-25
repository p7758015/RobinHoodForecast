"""Competition family taxonomy (men / women / youth / reserves / special)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CompetitionFamily(str, Enum):
    MEN_SENIOR_LEAGUE = "MEN_SENIOR_LEAGUE"
    WOMEN_SENIOR_LEAGUE = "WOMEN_SENIOR_LEAGUE"
    YOUTH_UXX = "YOUTH_UXX"
    RESERVES = "RESERVES"
    OTHER_SPECIAL = "OTHER_SPECIAL"


_WOMEN_CODE_RE = re.compile(r"_WOMEN\b|_FEMININ|_FEMININE|_FEMINAS\b", re.I)
_WOMEN_NAME_RE = re.compile(
    r"\b(women|woman|womens|ladies|feminino|feminina|féminin|feminin|feminas|frauen|damallsvenskan)\b",
    re.I,
)
_YOUTH_CODE_RE = re.compile(r"_U(?:1[0-9]|[0-9]{1,2})\b|_YOUTH\b|_JUNIOR\b|_JUVEN", re.I)
_YOUTH_NAME_RE = re.compile(
    r"\b(u-?\s?(?:1[0-9]|[0-9]{1,2})|sub-?\s?(?:1[0-9]|[0-9]{1,2})|"
    r"youth|junior|juniors|juvenil|primavera|academy|cadete)\b",
    re.I,
)
_RESERVE_CODE_RE = re.compile(r"_RESERVES?\b|_B_TEAMS?\b|_II\b", re.I)
_RESERVE_NAME_RE = re.compile(
    r"\b(reserves?|reserve\s+liga|b\s+team|team\s+b|segunda\s+equipe|"
    r"\bii\b|\b2nd\s+team)\b",
    re.I,
)
_SPECIAL_NAME_RE = re.compile(
    r"\b(play[- ]?off\s+only|regional\s+cup|mixed|futsal|beach)\b",
    re.I,
)


@dataclass(frozen=True)
class CompetitionFamilyMeta:
    family: CompetitionFamily
    subtype: Optional[str] = None
    is_women: bool = False
    is_youth: bool = False
    is_reserve: bool = False
    signals: tuple[str, ...] = ()
    source: str = "heuristic"  # registry | competition_code | competition_name | default

    @property
    def is_men_senior(self) -> bool:
        return self.family == CompetitionFamily.MEN_SENIOR_LEAGUE

    def to_debug_dict(self) -> dict:
        return {
            "competition_family": self.family.value,
            "competition_subtype": self.subtype,
            "is_women": self.is_women,
            "is_youth": self.is_youth,
            "is_reserve": self.is_reserve,
            "signals": list(self.signals),
            "source": self.source,
        }


def _norm(text: Optional[str]) -> str:
    return (text or "").strip()


def _youth_subtype(text: str) -> Optional[str]:
    m = re.search(r"\bU-?\s?(1[0-9]|[0-9]{1,2})\b", text, re.I)
    if m:
        return f"U{m.group(1).upper().replace(' ', '')}"
    m = re.search(r"\bSUB-?\s?(1[0-9]|[0-9]{1,2})\b", text, re.I)
    if m:
        return f"U{m.group(1).upper().replace(' ', '')}"
    return None


def classify_competition_family(
    *,
    competition_code: Optional[str] = None,
    competition_name: Optional[str] = None,
    country: Optional[str] = None,
) -> CompetitionFamilyMeta:
    """
    Classify competition family from registry code, FS slug code, and/or display name.

    Registry entry wins when ``competition_code`` is a known registry code.
    """
    from football_agent.league_registry import get_league_config

    code = _norm(competition_code).upper()
    name = _norm(competition_name)
    blob = f"{code} {name}".strip()
    signals: list[str] = []

    cfg = get_league_config(code) if code else None
    if cfg is not None and cfg.competition_family:
        fam = CompetitionFamily(cfg.competition_family)
        return CompetitionFamilyMeta(
            family=fam,
            subtype=cfg.competition_subtype,
            is_women=fam == CompetitionFamily.WOMEN_SENIOR_LEAGUE,
            is_youth=fam == CompetitionFamily.YOUTH_UXX,
            is_reserve=fam == CompetitionFamily.RESERVES,
            signals=(f"registry:{code}",),
            source="registry",
        )

    if _WOMEN_CODE_RE.search(code) or _WOMEN_NAME_RE.search(name) or _WOMEN_NAME_RE.search(blob):
        signals.append("women")
        return CompetitionFamilyMeta(
            family=CompetitionFamily.WOMEN_SENIOR_LEAGUE,
            is_women=True,
            signals=tuple(signals),
            source="competition_code" if _WOMEN_CODE_RE.search(code) else "competition_name",
        )

    youth_sub = _youth_subtype(blob) or _youth_subtype(name)
    if _YOUTH_CODE_RE.search(code) or _YOUTH_NAME_RE.search(name) or youth_sub:
        signals.append("youth")
        if youth_sub:
            signals.append(youth_sub)
        return CompetitionFamilyMeta(
            family=CompetitionFamily.YOUTH_UXX,
            subtype=youth_sub,
            is_youth=True,
            signals=tuple(signals),
            source="competition_code" if _YOUTH_CODE_RE.search(code) else "competition_name",
        )

    if _RESERVE_CODE_RE.search(code) or _RESERVE_NAME_RE.search(name):
        signals.append("reserves")
        return CompetitionFamilyMeta(
            family=CompetitionFamily.RESERVES,
            is_reserve=True,
            signals=tuple(signals),
            source="competition_code" if _RESERVE_CODE_RE.search(code) else "competition_name",
        )

    if _SPECIAL_NAME_RE.search(name):
        signals.append("other_special")
        return CompetitionFamilyMeta(
            family=CompetitionFamily.OTHER_SPECIAL,
            signals=tuple(signals),
            source="competition_name",
        )

    # Default: adult men's domestic league baseline bucket.
    return CompetitionFamilyMeta(
        family=CompetitionFamily.MEN_SENIOR_LEAGUE,
        signals=("default_men_senior",),
        source="default",
    )


def family_for_registry_code(registry_code: str) -> CompetitionFamily:
    from football_agent.league_registry import get_league_config

    cfg = get_league_config(registry_code)
    if cfg and cfg.competition_family:
        return CompetitionFamily(cfg.competition_family)
    return classify_competition_family(
        competition_code=registry_code,
        competition_name=cfg.display_name if cfg else None,
        country=cfg.country if cfg else None,
    ).family


def pool_entry_accepts_family(registry_code: str, family: CompetitionFamily) -> bool:
    """True when fixture family matches the pool entry's expected registry family."""
    expected = family_for_registry_code(registry_code)
    return family == expected


def competition_code_slug(competition_name: str) -> str:
    if not competition_name:
        return "FS"
    slug = "_".join(re.sub(r"[^a-zA-Z0-9]+", " ", competition_name).strip().split())
    slug = slug[:20] if slug else "FS"
    return f"FS_{slug}".upper()


def resolve_competition_identity(
    competition_name: str,
    competition_country: Optional[str] = None,
) -> tuple[str, CompetitionFamilyMeta]:
    """Registry-aware competition code + family for snapshots and policy."""
    from football_agent.eval_pool.scope import resolve_pool_entry

    entry = resolve_pool_entry(competition_name, competition_country)
    code = entry.registry_code if entry else competition_code_slug(competition_name)
    meta = classify_competition_family(
        competition_code=code,
        competition_name=competition_name,
        country=competition_country,
    )
    return code, meta
