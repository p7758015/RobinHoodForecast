"""Bridge request/response models (league-match scope only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BridgeMode(str, Enum):
    PROTOTYPE = "prototype"
    LIVE_ASSISTED = "live_assisted"


@dataclass
class BridgeMatchInput:
    """Structured match input from query params or JSON body."""

    home_team: str
    away_team: str
    competition_name: Optional[str] = None
    competition_code: Optional[str] = None
    kickoff_utc: Optional[str] = None
    date: Optional[str] = None
    country: Optional[str] = None
    match_url: Optional[str] = None
    match_id: Optional[str] = None
    # Optional Flashscore hints (not required)
    home_form_summary: Optional[str] = None
    away_form_summary: Optional[str] = None
    standings_summary: Optional[str] = None

    @classmethod
    def from_params(cls, params: Dict[str, str]) -> "BridgeMatchInput":
        return cls(
            home_team=(params.get("home") or params.get("home_team") or "").strip(),
            away_team=(params.get("away") or params.get("away_team") or "").strip(),
            competition_name=(params.get("competition_name") or params.get("competition") or "").strip() or None,
            competition_code=(params.get("competition_code") or "").strip() or None,
            kickoff_utc=(params.get("kickoff_utc") or "").strip() or None,
            date=(params.get("date") or "").strip() or None,
            country=(params.get("country") or "").strip() or None,
            match_url=(params.get("url") or params.get("match_url") or "").strip() or None,
            match_id=(params.get("match_id") or "").strip() or None,
            home_form_summary=(params.get("home_form") or "").strip() or None,
            away_form_summary=(params.get("away_form") or "").strip() or None,
            standings_summary=(params.get("standings") or "").strip() or None,
        )


@dataclass
class BridgeEnvelope:
    """Optional wrapper for diagnostics; /v1/* returns inner payload for adapter compat."""

    payload: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    missing_blocks: List[str] = field(default_factory=list)
    bridge_mode: str = BridgeMode.PROTOTYPE.value
    completeness: float = 0.0
    raw_gateway_snippet: Optional[str] = None

    def to_context_response(self) -> Dict[str, Any]:
        out = dict(self.payload)
        existing = list(out.get("extraction_warnings") or [])
        out["extraction_warnings"] = existing + list(self.warnings)
        out["bridge_mode"] = self.bridge_mode
        if self.raw_gateway_snippet:
            out["raw_gateway_probe"] = self.raw_gateway_snippet[:500]
        return out
