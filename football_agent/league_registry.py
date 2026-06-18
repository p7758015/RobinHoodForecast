"""
Central registry for domestic league competitions (league pipeline).

Discovery policy (FootballDataClient):
- ``LEAGUE_DISCOVERY_CODES`` env unset → discover only registry entries with
  ``football_data_discoverable=True`` (legacy top-5 European leagues by default).
- ``LEAGUE_DISCOVERY_CODES`` set (comma-separated) → discover only those codes.
- Auto-discovery of all world leagues never happens under any configuration.

Analysis / express policy (Stage 3 — competition-agnostic):
- No hardcoded top-5-only express filter in code.
- Allow/deny is controlled per ``LeagueConfig`` and optional env lists
  (see ``competition_policy``): ``LEAGUE_ANALYSIS_ALLOWED_CODES``,
  ``LEAGUE_EXPRESS_ALLOWED_CODES``, ``LEAGUE_DENY_CODES``.
- Flashscore-derived codes (``FS_*``) and other registered non-FootballData leagues
  (e.g. Botola) are first-class registry entries with ``football_data_discoverable=False``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, TypedDict

UNKNOWN_TOTAL_ROUNDS_WARNING = "unknown_total_rounds_for_competition"

DEFAULT_RELEGATION_SLOTS = 3
DEFAULT_EURO_SLOTS: Dict[str, int] = {"ucl": 4, "uel": 2}


class EuroSlots(TypedDict):
    ucl: int
    uel: int


@dataclass(frozen=True)
class LeagueConfig:
    competition_code: str
    display_name: str
    country: Optional[str] = None
    api_football_league_id: Optional[int] = None
    total_rounds: Optional[int] = None
    relegation_slots: int = DEFAULT_RELEGATION_SLOTS
    euro_slots: Optional[EuroSlots] = None
    # FootballData scheduled-match discovery (top-5 European codes by default).
    football_data_discoverable: bool = True
    # Product policy flags (overridable via env allow/deny lists).
    analysis_allowed: bool = True
    express_allowed: bool = True
    # Flashscore competition page (fixtures tab derived automatically).
    flashscore_competition_url: Optional[str] = None


@dataclass(frozen=True)
class LeagueParams:
    """Resolved parameters for a competition (known registry entry or defaults)."""

    competition_code: str
    display_name: str
    country: Optional[str]
    api_football_league_id: Optional[int]
    total_rounds: Optional[int]
    relegation_slots: int
    euro_slots: EuroSlots
    is_known: bool


_REGISTRY: Dict[str, LeagueConfig] = {
    "PL": LeagueConfig(
        competition_code="PL",
        display_name="Premier League",
        country="England",
        api_football_league_id=39,
        total_rounds=38,
    ),
    "PD": LeagueConfig(
        competition_code="PD",
        display_name="La Liga",
        country="Spain",
        api_football_league_id=140,
        total_rounds=38,
    ),
    "FL1": LeagueConfig(
        competition_code="FL1",
        display_name="Ligue 1",
        country="France",
        api_football_league_id=61,
        total_rounds=34,
    ),
    "BL1": LeagueConfig(
        competition_code="BL1",
        display_name="Bundesliga",
        country="Germany",
        api_football_league_id=78,
        total_rounds=34,
    ),
    "SA": LeagueConfig(
        competition_code="SA",
        display_name="Serie A",
        country="Italy",
        api_football_league_id=135,
        total_rounds=38,
    ),
    "FS_KAZAKHSTAN_PREMIER": LeagueConfig(
        competition_code="FS_KAZAKHSTAN_PREMIER",
        display_name="Kazakhstan Premier League",
        country="Kazakhstan",
        total_rounds=26,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/kazakhstan/premier-league/",
    ),
    "FS_ESTONIA_MEISTRILIIGA": LeagueConfig(
        competition_code="FS_ESTONIA_MEISTRILIIGA",
        display_name="Meistriliiga",
        country="Estonia",
        total_rounds=36,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/estonia/meistriliiga/",
    ),
    "FS_ESTONIA_PREMIUM_LIIGA": LeagueConfig(
        competition_code="FS_ESTONIA_PREMIUM_LIIGA",
        display_name="Premium Liiga",
        country="Estonia",
        total_rounds=36,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/estonia/meistriliiga-women/",
    ),
    "FS_LATVIA_VIRSLIGA": LeagueConfig(
        competition_code="FS_LATVIA_VIRSLIGA",
        display_name="Virsliga",
        country="Latvia",
        total_rounds=36,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/latvia/virsliga/",
    ),
    "FS_BRAZIL_SERIE_B": LeagueConfig(
        competition_code="FS_BRAZIL_SERIE_B",
        display_name="Brazil Serie B",
        country="Brazil",
        total_rounds=38,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/brazil/serie-b/",
    ),
    "FS_FINLAND_VEIKKAUSLIIGA": LeagueConfig(
        competition_code="FS_FINLAND_VEIKKAUSLIIGA",
        display_name="Veikkausliiga",
        country="Finland",
        total_rounds=22,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/finland/veikkausliiga/",
    ),
    "FS_BOTOLA_PRO": LeagueConfig(
        competition_code="FS_BOTOLA_PRO",
        display_name="Botola Pro",
        country="Morocco",
        total_rounds=30,
        football_data_discoverable=False,
        analysis_allowed=True,
        express_allowed=True,
        flashscore_competition_url="https://www.flashscore.com/football/morocco/botola-pro/",
    ),
    "FS_BELARUS_PREMIER": LeagueConfig(
        competition_code="FS_BELARUS_PREMIER",
        display_name="Vysshaya Liga",
        country="Belarus",
        total_rounds=30,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/belarus/vysshaya-liga/",
    ),
    "FS_CHILE_PRIMERA": LeagueConfig(
        competition_code="FS_CHILE_PRIMERA",
        display_name="Liga de Primera",
        country="Chile",
        total_rounds=30,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/chile/liga-de-primera/",
    ),
    "FS_LITHUANIA_A_LYGA": LeagueConfig(
        competition_code="FS_LITHUANIA_A_LYGA",
        display_name="A Lyga",
        country="Lithuania",
        total_rounds=36,
        football_data_discoverable=False,
        flashscore_competition_url="https://www.flashscore.com/football/lithuania/a-lyga/",
    ),
}


def _normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def _euro_slots(cfg: Optional[LeagueConfig]) -> EuroSlots:
    if cfg and cfg.euro_slots is not None:
        return cfg.euro_slots
    if cfg:
        # Per-league overrides from legacy config when not set on LeagueConfig
        legacy = _LEGACY_EURO_OVERRIDES.get(cfg.competition_code)
        if legacy:
            return legacy
    return {"ucl": DEFAULT_EURO_SLOTS["ucl"], "uel": DEFAULT_EURO_SLOTS["uel"]}


_LEGACY_EURO_OVERRIDES: Dict[str, EuroSlots] = {
    "FL1": {"ucl": 2, "uel": 3},
}


def get_league_config(competition_code: str) -> Optional[LeagueConfig]:
    return _REGISTRY.get(_normalize_code(competition_code))


def list_registry_codes() -> List[str]:
    """All competition codes defined in the registry (stable order)."""
    return sorted(_REGISTRY.keys())


def registry_league_display_names_lower() -> frozenset[str]:
    """Lowercased display names from the league registry (classifier hints)."""
    return frozenset(cfg.display_name.strip().lower() for cfg in _REGISTRY.values() if cfg.display_name)


def list_football_data_discovery_codes() -> List[str]:
    """Registry codes eligible for FootballData scheduled-match discovery."""
    return sorted(
        code for code, cfg in _REGISTRY.items() if cfg.football_data_discoverable
    )


def resolve_league_params(competition_code: str) -> LeagueParams:
    code = _normalize_code(competition_code)
    cfg = _REGISTRY.get(code)
    if cfg is None:
        return LeagueParams(
            competition_code=code or "UNKNOWN",
            display_name=code or "Unknown competition",
            country=None,
            api_football_league_id=None,
            total_rounds=None,
            relegation_slots=DEFAULT_RELEGATION_SLOTS,
            euro_slots={"ucl": DEFAULT_EURO_SLOTS["ucl"], "uel": DEFAULT_EURO_SLOTS["uel"]},
            is_known=False,
        )
    return LeagueParams(
        competition_code=cfg.competition_code,
        display_name=cfg.display_name,
        country=cfg.country,
        api_football_league_id=cfg.api_football_league_id,
        total_rounds=cfg.total_rounds,
        relegation_slots=cfg.relegation_slots,
        euro_slots=_euro_slots(cfg),
        is_known=True,
    )


def discovery_competition_codes() -> List[str]:
    """
    Codes used by FootballDataClient match discovery.

    - Env ``LEAGUE_DISCOVERY_CODES`` unset → registry entries with
      ``football_data_discoverable=True`` only.
    - Env set → only comma-separated codes (uppercased), still no global auto-discovery.
    """
    env = (os.getenv("LEAGUE_DISCOVERY_CODES") or "").strip()
    if env:
        return [_normalize_code(part) for part in env.split(",") if part.strip()]
    return list_football_data_discovery_codes()


def register_league(config: LeagueConfig) -> None:
    """Register or replace a league entry (tests / runtime extension)."""
    _REGISTRY[_normalize_code(config.competition_code)] = config


# ---------------------------------------------------------------------------
# Deprecated config aliases (backward compatibility)
# ---------------------------------------------------------------------------


def build_league_ids_football_data() -> Dict[str, int]:
    """Legacy map; numeric values unused by FootballDataClient (URL uses code)."""
    return {code: 0 for code in list_registry_codes()}


def build_league_ids_api_football() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for code in list_registry_codes():
        cfg = _REGISTRY[code]
        if cfg.api_football_league_id is not None:
            out[code] = cfg.api_football_league_id
    return out


def build_total_rounds() -> Dict[str, int]:
    return {
        code: cfg.total_rounds
        for code, cfg in _REGISTRY.items()
        if cfg.total_rounds is not None
    }


def build_relegation_slots() -> Dict[str, int]:
    return {code: cfg.relegation_slots for code, cfg in _REGISTRY.items()}


def build_euro_slots() -> Mapping[str, EuroSlots]:
    return {code: _euro_slots(cfg) for code, cfg in _REGISTRY.items()}
