"""Merge eval-pool discovery fixture hints into scraper match-detail raw payloads."""

from __future__ import annotations

from typing import Any, Dict, Optional

from football_agent.collectors.flashscore.validation import is_valid_competition_name


def merge_discovery_hints(raw: dict, hints: Optional[dict]) -> dict:
    """
    Overlay stable discovery/list fields when match-detail scrape is incomplete.

    Used when Flashscore match pages return junk ``competition_name`` or missing kickoff.
    """
    if not hints:
        return raw

    merged = dict(raw)

    for key in ("home_team_name", "away_team_name", "competition_country", "match_id", "status"):
        val = hints.get(key)
        if val and not merged.get(key):
            merged[key] = val

    hint_comp = str(hints.get("competition_name") or "").strip()
    comp_valid, _ = is_valid_competition_name(merged.get("competition_name"))
    if hint_comp and not comp_valid:
        merged["competition_name"] = hint_comp

    fixture_date = hints.get("fixture_date") or hints.get("date")
    kickoff = hints.get("kickoff_utc")
    if kickoff:
        merged["kickoff_utc"] = kickoff
    elif fixture_date and not merged.get("kickoff_utc"):
        merged["kickoff_utc"] = f"{fixture_date}T12:00:00+00:00"
        merged.setdefault("date", fixture_date)

    return merged
