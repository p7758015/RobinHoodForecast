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

# Operational wave extensions (eval waves beyond wave-1 default pool).
WAVE_EXTENSION_POOL: Tuple[LeaguePoolEntry, ...] = (
    LeaguePoolEntry(
        key="finland_veikkausliiga",
        display_name="Finland Veikkausliiga",
        countries=("finland",),
        name_patterns=("veikkausliiga", "finland"),
        registry_code="FS_FINLAND_VEIKKAUSLIIGA",
    ),
    LeaguePoolEntry(
        key="morocco_botola",
        display_name="Morocco Botola Pro",
        countries=("morocco",),
        name_patterns=("botola",),
        registry_code="FS_BOTOLA_PRO",
    ),
    LeaguePoolEntry(
        key="belarus_premier",
        display_name="Belarus Premier League",
        countries=("belarus",),
        name_patterns=("belarus", "vysshaya", "vysheyshaya", "premier league"),
        registry_code="FS_BELARUS_PREMIER",
    ),
    LeaguePoolEntry(
        key="chile_primera",
        display_name="Chile Primera Division",
        countries=("chile",),
        name_patterns=("primera", "chile"),
        registry_code="FS_CHILE_PRIMERA",
    ),
    LeaguePoolEntry(
        key="lithuania_a_lyga",
        display_name="Lithuania A Lyga",
        countries=("lithuania",),
        name_patterns=("a lyga", "alyga", "lithuania"),
        registry_code="FS_LITHUANIA_A_LYGA",
    ),
)

WAVE1_POOL_KEYS: Tuple[str, ...] = tuple(e.key for e in WAVE1_LEAGUE_POOL)


def all_pool_entries() -> Tuple[LeaguePoolEntry, ...]:
    return WAVE1_LEAGUE_POOL + WAVE_EXTENSION_POOL


def resolve_pool_entry(
    competition_name: Optional[str],
    competition_country: Optional[str] = None,
) -> Optional[LeaguePoolEntry]:
    """Return wave-1 pool entry when competition name + country match."""
    comp = _norm(competition_name)
    if not comp:
        return None
    country = _norm(competition_country)
    for entry in all_pool_entries():
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
    selected = tuple(e for e in all_pool_entries() if e.key in wanted)
    if not selected:
        raise ValueError(f"No pool entries for keys: {list(keys)}")
    return selected
