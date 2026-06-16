"""League registry and discovery policy."""

from __future__ import annotations

import os
from unittest.mock import patch

from football_agent.league_registry import (
    UNKNOWN_TOTAL_ROUNDS_WARNING,
    LeagueConfig,
    discovery_competition_codes,
    list_football_data_discovery_codes,
    list_registry_codes,
    register_league,
    resolve_league_params,
)


def test_registry_top5_total_rounds() -> None:
    pl = resolve_league_params("PL")
    assert pl.is_known is True
    assert pl.total_rounds == 38
    assert pl.api_football_league_id == 39
    assert pl.relegation_slots == 3


def test_unknown_league_defaults() -> None:
    params = resolve_league_params("XYZ")
    assert params.is_known is False
    assert params.total_rounds is None
    assert params.relegation_slots == 3
    assert params.euro_slots == {"ucl": 4, "uel": 2}
    assert params.api_football_league_id is None


def test_fl1_euro_slots_override() -> None:
    fl1 = resolve_league_params("FL1")
    assert fl1.euro_slots == {"ucl": 2, "uel": 3}


def test_discovery_default_football_data_codes_only() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LEAGUE_DISCOVERY_CODES", None)
        codes = discovery_competition_codes()
    assert codes == list_football_data_discovery_codes()
    assert "PL" in codes
    assert len(codes) == 5
    assert "FS_BOTOLA_PRO" in list_registry_codes()
    assert "FS_BOTOLA_PRO" not in codes


def test_botola_registry_entry() -> None:
    botola = resolve_league_params("FS_BOTOLA_PRO")
    assert botola.is_known is True
    assert botola.total_rounds == 30
    assert botola.display_name == "Botola Pro"


def test_discovery_env_override() -> None:
    with patch.dict(os.environ, {"LEAGUE_DISCOVERY_CODES": "pl, elc"}):
        assert discovery_competition_codes() == ["PL", "ELC"]


def test_register_league_runtime() -> None:
    register_league(
        LeagueConfig(
            competition_code="TESTL",
            display_name="Test League",
            total_rounds=30,
            api_football_league_id=999,
        )
    )
    try:
        p = resolve_league_params("TESTL")
        assert p.total_rounds == 30
        assert p.api_football_league_id == 999
    finally:
        from football_agent.league_registry import _REGISTRY

        _REGISTRY.pop("TESTL", None)


def test_config_deprecated_aliases_match_registry() -> None:
    from football_agent import config

    assert config.TOTAL_ROUNDS["PL"] == 38
    assert config.LEAGUE_IDS_API_FOOTBALL["PL"] == 39
    assert config.RELEGATION_SLOTS["PL"] == 3
