"""Structured outcomes for live HTTP source fetches (OpenClaw, odds, etc.)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class HttpFetchOutcome:
    source: str
    status: str  # ok | failed | skipped
    failure_reason: Optional[str] = None  # timeout | auth | bad_payload | empty | unavailable | link_mismatch
    warnings: List[str] = field(default_factory=list)

    def as_source_dict(self) -> Dict[str, str]:
        return {self.source: self.status}


def classify_http_error_message(message: str) -> str:
    low = (message or "").lower()
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "http 401" in low or "http 403" in low or "unauthorized" in low or "forbidden" in low:
        return "auth"
    if "invalid json" in low or "expected json" in low or "empty" in low:
        return "bad_payload"
    if "request failed" in low or "unavailable" in low or re.search(r"http 5\d\d", low):
        return "unavailable"
    return "unavailable"
