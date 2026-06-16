"""Pre-kickoff odds freshness helpers (Refresh A)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_odds_stale(
    collected_at_utc: datetime,
    kickoff_utc: Optional[datetime],
    now_utc: datetime,
    *,
    max_age_minutes: int = 60,
    pre_kickoff_window_minutes: int = 120,
) -> bool:
    """
    Return True when collector odds should be refreshed before kickoff.

    Rules (deterministic, no scheduler):
    - Match already started/finished → not stale (pre-kickoff refresh N/A).
    - Age <= max_age_minutes → fresh.
    - Age > max_age_minutes and match not started → stale.
    - Inside pre-kickoff window: use half of max_age as tighter threshold.
    """
    collected = _ensure_utc(collected_at_utc)
    now = _ensure_utc(now_utc)

    if kickoff_utc is not None:
        kickoff = _ensure_utc(kickoff_utc)
        if now >= kickoff:
            return False

    age_minutes = (now - collected).total_seconds() / 60.0
    if age_minutes <= max_age_minutes:
        return False

    if kickoff_utc is not None:
        kickoff = _ensure_utc(kickoff_utc)
        minutes_to_kickoff = (kickoff - now).total_seconds() / 60.0
        if 0 < minutes_to_kickoff <= pre_kickoff_window_minutes:
            tight_threshold = max(1.0, max_age_minutes / 2.0)
            return age_minutes > tight_threshold

    return age_minutes > max_age_minutes


def freshness_status_for(
    collected_at_utc: datetime,
    kickoff_utc: Optional[datetime],
    now_utc: datetime,
    *,
    max_age_minutes: int = 60,
    pre_kickoff_window_minutes: int = 120,
) -> str:
    if is_odds_stale(
        collected_at_utc,
        kickoff_utc,
        now_utc,
        max_age_minutes=max_age_minutes,
        pre_kickoff_window_minutes=pre_kickoff_window_minutes,
    ):
        return "stale"
    return "fresh"
