"""
Fixture adapter for a Flashscore scraper backend (e.g. gustavofariaa/FlashscoreScraping).

Loads JSON from disk for tests and offline traces.
Live HTTP ingestion: ``flashscore.adapters.http_backend.HttpFlashscoreScraperAdapter``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import FlashscoreScraperAdapter


class FixtureFileFlashscoreAdapter(FlashscoreScraperAdapter):
    """
    Simple backend that reads raw Flashscore payloads from JSON fixture files.

    This allows us to validate the normalized contract and mapping before wiring
    to a real scraper runtime.
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def _read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def fetch_matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Load a list of raw records from ``{fixtures_dir}/{date_str}.json``.

        Expected file format (example):
        [
          { ... raw_match1 ... },
          { ... raw_match2 ... }
        ]
        """

        path = self._fixtures_dir / f"{date_str}.json"
        if not path.exists():
            return []
        data = self._read_json(path)
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                if competition_code and str(item.get("competition_code") or "").upper() != competition_code.upper():
                    continue
                out.append(item)
        return out

    def fetch_match_raw(self, match_id_or_url: str) -> Dict[str, Any]:
        """
        Load a single raw record from a file named ``{match_id_or_url}.json``.

        For sample data we treat ``match_id_or_url`` as filename stem.
        """

        path = self._fixtures_dir / f"{match_id_or_url}.json"
        if not path.exists():
            return {}
        data = self._read_json(path)
        return data if isinstance(data, dict) else {}

