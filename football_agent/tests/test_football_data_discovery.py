"""FootballDataClient discovery uses registry / env, not hardcoded top-5 only."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from football_agent.data_providers.football_data_client import FootballDataClient
def test_get_matches_by_date_uses_registry_codes() -> None:
    client = FootballDataClient("test-key")
    client._get = MagicMock(return_value={"matches": []})  # type: ignore[method-assign]

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LEAGUE_DISCOVERY_CODES", None)
        client.get_matches_by_date("2024-04-25")

    called_paths = [args[0] for args, _kwargs in client._get.call_args_list]
    assert "/competitions/PL/matches" in called_paths
    assert "/competitions/SA/matches" in called_paths
    assert len(called_paths) == 5


def test_get_matches_explicit_competition_codes() -> None:
    client = FootballDataClient("test-key")
    client._get = MagicMock(return_value={"matches": []})  # type: ignore[method-assign]

    client.get_matches_by_date("2024-04-25", competition_codes=["ELC"])

    client._get.assert_called_once()
    assert client._get.call_args[0][0] == "/competitions/ELC/matches"


def test_env_discovery_override() -> None:
    client = FootballDataClient("test-key")
    client._get = MagicMock(return_value={"matches": []})  # type: ignore[method-assign]

    with patch.dict(os.environ, {"LEAGUE_DISCOVERY_CODES": "ELC,PL"}):
        client.get_matches_by_date("2024-04-25")

    assert client._get.call_count == 2
    paths = [c[0][0] for c in client._get.call_args_list]
    assert "/competitions/ELC/matches" in paths
    assert "/competitions/PL/matches" in paths
