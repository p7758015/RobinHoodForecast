"""Adapter boundary for external Flashscore scraper backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class FlashscoreScraperAdapter(ABC):
    """
    Abstract adapter for any Flashscore scraper backend.

    Backends may be:
    - a self-hosted HTTP API,
    - a CLI tool that writes JSON files,
    - a local subprocess wrapper,
    - or a test fixture loader.

    This layer knows about *raw* scraper payloads only (dicts),
    not about internal FlashscoreMatchFacts.
    """

    @abstractmethod
    def fetch_matches_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return raw scraper payloads for all matches on date (optionally filtered by competition)."""

    @abstractmethod
    def fetch_match_raw(self, match_id_or_url: str) -> Dict[str, Any]:
        """Return raw scraper payload for a single match, referenced by id or URL."""

