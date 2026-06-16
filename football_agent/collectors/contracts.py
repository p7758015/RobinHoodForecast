"""Collector layer contracts (Phase A)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from football_agent.domain.models_v2 import V2IngestModel
from football_agent.flashscore.models import FlashscoreMatchFacts

BlockStatus = Literal["ok", "partial", "missing", "failed"]
BundleStatus = Literal["ok", "partial", "failed", "aborted"]
AttemptStatus = Literal["ok", "partial", "missing", "failed", "skipped"]
WarningSeverity = Literal["info", "warn", "error"]

# Collector block identifiers (Phase A/B + Odds A)
BLOCK_MATCH_META = "match_meta"
BLOCK_TEAMS = "teams"
BLOCK_FORM = "form"
BLOCK_ODDS = "odds"

# Canonical prematch market keys (Odds A — Flashscore-first, no derived fills)
ODDS_MARKET_KEYS: tuple[str, ...] = (
    "HOME_WIN",
    "DRAW",
    "AWAY_WIN",
    "HOME_OR_DRAW",
    "AWAY_OR_DRAW",
    "OVER_1_5",
    "UNDER_3_5",
    "BTTS_YES",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MatchRef(V2IngestModel):
    """Match-centric collection input."""

    match_url: Optional[str] = None
    match_id: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    date: Optional[str] = None
    competition_code: Optional[str] = None


class SourceAttempt(V2IngestModel):
    block: str
    source: str
    started_at_utc: datetime
    finished_at_utc: datetime
    status: AttemptStatus
    http_status: Optional[int] = None
    error_code: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    raw_ref: Optional[str] = None
    duration_ms: int = 0


class CollectorWarning(V2IngestModel):
    code: str
    block: str
    message: str
    severity: WarningSeverity = "warn"


class BlockCollectionResult(V2IngestModel):
    block: str
    status: BlockStatus
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    collected_at_utc: datetime
    payload: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    attempts: List[SourceAttempt] = Field(default_factory=list)
    raw_ref: Optional[str] = None


class MatchCollectionBundle(V2IngestModel):
    match_key: str
    match_ref: MatchRef
    blocks: Dict[str, BlockCollectionResult] = Field(default_factory=dict)
    flashscore_facts: Optional[FlashscoreMatchFacts] = None
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_status: BundleStatus = "partial"
    trace_id: str = ""
    aborted: bool = False
    abort_reason: Optional[str] = None


class CollectionTrace(V2IngestModel):
    trace_id: str
    match_key: str
    started_at_utc: datetime
    finished_at_utc: Optional[datetime] = None
    attempts: List[SourceAttempt] = Field(default_factory=list)
    warnings: List[CollectorWarning] = Field(default_factory=list)
    block_confidence: Dict[str, float] = Field(default_factory=dict)
    block_status: Dict[str, str] = Field(default_factory=dict)
    raw_refs: Dict[str, str] = Field(default_factory=dict)
