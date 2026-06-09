"""
Flashscore ingestion layer: external scraper output → normalized facts.

This package intentionally does NOT depend on v2 snapshots, scorers, or OpenClaw.
It only defines:
- typed contract for normalized Flashscore facts (`models.py`)
- adapter boundary to external scraper runtimes (`adapters/`)
- a single public entrypoint service for consumers (`service.py`)
"""

from .models import FlashscoreMatchFacts
from .service import FlashscoreIngestionService

__all__ = ["FlashscoreMatchFacts", "FlashscoreIngestionService"]

