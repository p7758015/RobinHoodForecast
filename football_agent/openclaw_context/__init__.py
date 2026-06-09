"""
OpenClaw context ingestion layer (secondary match context/news source).

This package is intentionally isolated:
- no HTTP/runtime integration in this step (fixture-based only)
- no merge with Flashscore facts
- no v2 snapshots / scorers / pipeline wiring
"""

from .models import OpenClawMatchContext
from .service import OpenClawContextIngestionService

__all__ = ["OpenClawMatchContext", "OpenClawContextIngestionService"]

