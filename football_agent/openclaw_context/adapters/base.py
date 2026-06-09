"""Adapter boundary for external OpenClaw context backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class OpenClawContextAdapter(ABC):
    """Abstract adapter for any OpenClaw context backend (fixture/http/cli/subprocess)."""

    @abstractmethod
    def fetch_context_raw(self, fixture_id_or_query: str) -> Dict[str, Any]:
        """Return raw OpenClaw context payload as a dict (backend-specific shape)."""

