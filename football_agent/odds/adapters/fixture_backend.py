"""Fixture-based odds backend (no runtime integration)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .base import OddsAdapter


class FixtureFileOddsAdapter(OddsAdapter):
    """
    Load raw odds payload from `{fixtures_dir}/{fixture}.json`.
    `fixture_id_or_query` is treated as filename stem.
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def fetch_odds_raw(self, fixture_id_or_query: str) -> Dict[str, Any]:
        path = self._fixtures_dir / f"{fixture_id_or_query}.json"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

