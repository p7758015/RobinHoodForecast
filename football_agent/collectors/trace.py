"""Collection trace builder (Phase A)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from football_agent.collectors.contracts import (
    BlockCollectionResult,
    CollectionTrace,
    CollectorWarning,
    SourceAttempt,
    utc_now,
)


class CollectionTraceBuilder:
    def __init__(self, match_key: str) -> None:
        self._trace_id = uuid.uuid4().hex[:16]
        self._match_key = match_key
        self._started = utc_now()
        self._attempts: List[SourceAttempt] = []
        self._warnings: List[CollectorWarning] = []
        self._block_confidence: dict[str, float] = {}
        self._block_status: dict[str, str] = {}
        self._raw_refs: dict[str, str] = {}

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def record_block(self, result: BlockCollectionResult) -> None:
        self._block_confidence[result.block] = result.confidence
        self._block_status[result.block] = result.status
        if result.raw_ref:
            self._raw_refs[result.block] = result.raw_ref
        self._attempts.extend(result.attempts)
        for code in result.warnings:
            self._warnings.append(
                CollectorWarning(code=code, block=result.block, message=code),
            )

    def add_warning(self, code: str, block: str, message: str, *, severity: str = "warn") -> None:
        self._warnings.append(
            CollectorWarning(code=code, block=block, message=message, severity=severity),  # type: ignore[arg-type]
        )

    def finish(self) -> CollectionTrace:
        return CollectionTrace(
            trace_id=self._trace_id,
            match_key=self._match_key,
            started_at_utc=self._started,
            finished_at_utc=utc_now(),
            attempts=self._attempts,
            warnings=self._warnings,
            block_confidence=dict(self._block_confidence),
            block_status=dict(self._block_status),
            raw_refs=dict(self._raw_refs),
        )
