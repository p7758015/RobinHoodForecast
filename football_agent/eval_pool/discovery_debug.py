"""Debug helpers for eval-pool discovery date filtering."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from football_agent.eval_pool.fixture_date import extract_fixture_date, filter_fixtures_in_date_range
from football_agent.eval_pool.fixture_sources import discovery_query_for_pool_entry, fetch_fixtures_for_pool_entry
from football_agent.eval_pool.scope import LeaguePoolEntry, filter_pool_keys


def debug_pool_entry_discovery(
    pool_key: str,
    *,
    date_from: str,
    date_to: str,
    use_discovery_fallback: bool = True,
) -> Dict[str, Any]:
    """
    Run the same discovery path as accumulate-wave for one pool entry.

    Returns counts and sample fixtures for manual validation.
    """
    entries = filter_pool_keys([pool_key])
    if not entries:
        raise ValueError(f"unknown pool key: {pool_key}")
    entry = entries[0]

    result = fetch_fixtures_for_pool_entry(
        entry,
        date_from,
        [],
        use_discovery_fallback=use_discovery_fallback,
        wave_date_from=date_from,
        wave_date_to=date_to,
    )
    samples: List[Dict[str, Any]] = []
    for raw in result.fixtures[:10]:
        samples.append(
            {
                "match_id": raw.get("match_id"),
                "home": raw.get("home_team_name"),
                "away": raw.get("away_team_name"),
                "time": raw.get("time"),
                "date": raw.get("date"),
                "kickoff_utc": raw.get("kickoff_utc"),
                "fixture_date": extract_fixture_date(raw),
            }
        )
    return {
        "pool_key": entry.key,
        "query": discovery_query_for_pool_entry(entry),
        "date_from": date_from,
        "date_to": date_to,
        "stats": {
            "seen": result.stats.seen,
            "in_range": result.stats.in_range,
            "skipped_out_of_range": result.stats.skipped_out_of_range,
        },
        "warnings": result.warnings,
        "samples_in_range": samples,
    }
