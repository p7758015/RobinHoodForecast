"""Odds freshness helper tests (Refresh A)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from football_agent.services.odds_freshness import freshness_status_for, is_odds_stale

UTC = timezone.utc


def test_stale_when_collected_long_ago_and_match_not_started() -> None:
    now = datetime(2025, 6, 3, 10, 0, tzinfo=UTC)
    collected = now - timedelta(hours=3)
    kickoff = datetime(2025, 6, 3, 18, 0, tzinfo=UTC)
    assert is_odds_stale(collected, kickoff, now, max_age_minutes=60) is True
    assert freshness_status_for(collected, kickoff, now, max_age_minutes=60) == "stale"


def test_fresh_when_recent_collection() -> None:
    now = datetime(2025, 6, 3, 10, 0, tzinfo=UTC)
    collected = now - timedelta(minutes=20)
    kickoff = datetime(2025, 6, 3, 18, 0, tzinfo=UTC)
    assert is_odds_stale(collected, kickoff, now, max_age_minutes=60) is False


def test_not_stale_when_match_already_started() -> None:
    now = datetime(2025, 6, 3, 19, 0, tzinfo=UTC)
    collected = now - timedelta(hours=5)
    kickoff = datetime(2025, 6, 3, 18, 0, tzinfo=UTC)
    assert is_odds_stale(collected, kickoff, now, max_age_minutes=60) is False
