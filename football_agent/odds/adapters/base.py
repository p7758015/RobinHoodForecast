"""Adapter boundary for external odds backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class OddsAdapter(ABC):
    """Abstract adapter for raw odds payload retrieval (fixture/http/cli/subprocess)."""

    @abstractmethod
    def fetch_odds_raw(self, fixture_id_or_query: str) -> Dict[str, Any]:
        """Return backend-specific raw odds payload as dict."""

