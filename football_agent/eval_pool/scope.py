"""
Wave-1 league eval-pool scope (active live competitions).

Used by accumulation/settlement/report runners to filter league-eligible matches
without changing routing shell or parked semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


LOW_CONFIDENCE_THRESHOLD = 0.45


@dataclass(frozen=True)
class LeaguePoolEntry:
    key: str
    display_name: str
    countries: Tuple[str, ...]
    name_patterns: Tuple[str, ...]
    registry_code: str


WAVE1_LEAGUE_POOL: Tuple[LeaguePoolEntry, ...] = (
    LeaguePoolEntry(
        key="kazakhstan_premier",
        display_name="Kazakhstan Premier League",
        countries=("kazakhstan",),
        name_patterns=("premier league", "kazakhstan premier", "qazaqstan premier"),
        registry_code="FS_KAZAKHSTAN_PREMIER",
    ),
    LeaguePoolEntry(
        key="estonia_meistriliiga",
        display_name="Estonia Meistriliiga",
        countries=("estonia",),
        name_patterns=("meistriliiga",),
        registry_code="FS_ESTONIA_MEISTRILIIGA",
    ),
    LeaguePoolEntry(
        key="estonia_premium_liiga",
        display_name="Estonia Premium Liiga",
        countries=("estonia",),
        name_patterns=("premium liiga",),
        registry_code="FS_ESTONIA_PREMIUM_LIIGA",
    ),
    LeaguePoolEntry(
        key="latvia_virsliga",
        display_name="Latvia Virsliga",
        countries=("latvia",),
        name_patterns=("virsliga", "virslīga"),
        registry_code="FS_LATVIA_VIRSLIGA",
    ),
    LeaguePoolEntry(
        key="brazil_serie_b",
        display_name="Brazil Serie B",
        countries=("brazil",),
        name_patterns=("serie b", "brasileirao serie b", "campeonato brasileiro serie b"),
        registry_code="FS_BRAZIL_SERIE_B",
    ),
)

WAVE1_POOL_KEYS: Tuple[str, ...] = tuple(e.key for e in WAVE1_LEAGUE_POOL)


def resolve_pool_entry(
    competition_name: Optional[str],
    competition_country: Optional[str] = None,
) -> Optional[LeaguePoolEntry]:
    """Return wave-1 pool entry when competition name + country match."""
    comp = _norm(competition_name)
    if not comp:
        return None
    country = _norm(competition_country)
    for entry in WAVE1_LEAGUE_POOL:
        name_ok = any(pat in comp for pat in entry.name_patterns)
        if not name_ok:
            continue
        if entry.countries:
            country_ok = country in entry.countries or any(c in comp for c in entry.countries)
            if not country_ok:
                continue
        return entry
    return None


def filter_pool_keys(keys: Optional[Sequence[str]]) -> Tuple[LeaguePoolEntry, ...]:
    if not keys:
        return WAVE1_LEAGUE_POOL
    wanted = {_norm(k) for k in keys}
    selected = tuple(e for e in WAVE1_LEAGUE_POOL if e.key in wanted)
    if not selected:
        raise ValueError(f"No wave-1 pool entries for keys: {list(keys)}")
    return selected
