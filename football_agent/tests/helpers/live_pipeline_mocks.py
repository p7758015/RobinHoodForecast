"""Helpers for live pipeline ingest mocks (collector vs legacy)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from football_agent.collectors.contracts import MatchCollectionBundle
from football_agent.flashscore.models import FlashscoreMatchFacts


def legacy_ingest_return(
    facts: FlashscoreMatchFacts,
    *,
    sources: Optional[dict] = None,
    error: Optional[str] = None,
) -> Tuple[Optional[FlashscoreMatchFacts], dict, Optional[str]]:
    return facts, sources or {"flashscore": "ok"}, error


def collector_ingest_return(
    facts: Optional[FlashscoreMatchFacts],
    *,
    sources: Optional[dict] = None,
    error: Optional[str] = None,
    warnings: Optional[List[str]] = None,
    bundle: Optional[MatchCollectionBundle] = None,
) -> Tuple[
    Optional[FlashscoreMatchFacts],
    dict,
    Optional[str],
    List[str],
    Optional[MatchCollectionBundle],
]:
    src = sources or {"flashscore": "ok" if facts else "failed", "collector": "ok" if facts else "aborted"}
    return facts, src, error, list(warnings or []), bundle


def collector_aborted_return(
    *,
    reason: str = "match_meta_failed",
    error_message: str = "Flashscore match metadata invalid (teams or competition).",
) -> Tuple[None, dict, str, List[str], None]:
    return collector_ingest_return(
        None,
        sources={"flashscore": "failed", "collector": "aborted"},
        error=error_message,
        warnings=[f"collector_aborted:{reason}"],
        bundle=None,
    )
