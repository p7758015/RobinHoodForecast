"""
Central registry for domestic league competitions (league pipeline).

Discovery policy (FootballDataClient):
- ``LEAGUE_DISCOVERY_CODES`` env unset → discover only codes registered here.
- ``LEAGUE_DISCOVERY_CODES`` set (comma-separated) → discover only those codes.
- Auto-discovery of all world leagues never happens under any configuration.
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

    - Env ``LEAGUE_DISCOVERY_CODES`` unset → all registry codes only.
    - Env set → only comma-separated codes (uppercased), still no global auto-discovery.
    """
    env = (os.getenv("LEAGUE_DISCOVERY_CODES") or "").strip()
    if env:
        return [_normalize_code(part) for part in env.split(",") if part.strip()]
    return list_registry_codes()


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
