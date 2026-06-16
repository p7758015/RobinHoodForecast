"""
Odds ingestion layer (v1): external raw odds payload → normalized odds contract.

Important: this v1 contract intentionally does NOT include a pure DRAW/X market.
Only the explicitly required markets are modeled (see `models.py`).
"""

from .coverage import build_match_odds_coverage, enrich_odds_context_with_coverage
from .coverage_models import MatchOddsCoverage, MarketOddsCoverageEntry
from .models import MatchOddsContext
from .service import OddsIngestionService

__all__ = [
    "MatchOddsContext",
    "MatchOddsCoverage",
    "MarketOddsCoverageEntry",
    "OddsIngestionService",
    "build_match_odds_coverage",
    "enrich_odds_context_with_coverage",
]

