"""
Deterministic match_key utilities for persistence (storage-level contract).

Design goals:
- narrow, explicit inputs (no "magic" inference from arbitrary objects)
- deterministic and stable across runs
- safe for use as a lookup key in SQLite

Canonical format:
    "{competition}|{kickoff_date_utc}|{home}|{away}"

where each component is normalized:
- competition: normalized code/name (lowercase, alnum + '_' only)
- kickoff_date_utc: YYYY-MM-DD, or 'unknown-date' when missing
- home/away: normalized team names (lowercase, collapse spaces, '_' separators)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from football_agent.analysis_merge.models import MergedMatchAnalysisContext


def build_match_key(
    *,
    competition: str,
    kickoff_utc: Optional[datetime],
    home_team: str,
    away_team: str,
) -> str:
    """Build deterministic match_key from explicit typed fields."""
    comp = normalize_competition_for_key(competition)
    date_str = _kickoff_date_utc_str(kickoff_utc)
    home = normalize_team_for_key(home_team)
    away = normalize_team_for_key(away_team)
    return f"{comp}|{date_str}|{home}|{away}"


def build_match_key_from_merged(merged: MergedMatchAnalysisContext) -> str:
    """
    Thin wrapper: build match_key strictly from `merged.headline`.

    This function does NOT guess fields from deep trees; it uses only:
    - competition_name
    - kickoff_utc
    - home_team
    - away_team
    """
    h = merged.headline
    return build_match_key(
        competition=str(h.competition_name or ""),
        kickoff_utc=h.kickoff_utc,
        home_team=str(h.home_team or ""),
        away_team=str(h.away_team or ""),
    )


def _kickoff_date_utc_str(kickoff_utc: Optional[datetime]) -> str:
    if kickoff_utc is None:
        return "unknown-date"
    dt = kickoff_utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def normalize_competition_for_key(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "unknown-competition"


def normalize_team_for_key(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "unknown-team"

