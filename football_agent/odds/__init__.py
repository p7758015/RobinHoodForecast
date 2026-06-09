"""
Odds ingestion layer (v1): external raw odds payload → normalized odds contract.

Important: this v1 contract intentionally does NOT include a pure DRAW/X market.
Only the explicitly required markets are modeled (see `models.py`).
"""

from .models import MatchOddsContext
from .service import OddsIngestionService

__all__ = ["MatchOddsContext", "OddsIngestionService"]

