"""Collect fixtures for eval-pool: list-by-date filter + optional discovery fallback."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.eval_pool.scope import LeaguePoolEntry, resolve_pool_entry

logger = logging.getLogger(__name__)

# Scraper list-by-code paths (legacy); FS_* wave-1 entries skip this path.
_LEGACY_LIST_CODES = frozenset({"PL", "CL", "BL1", "SA", "PD", "FL1"})


def _raw_matches_pool_entry(raw: dict, entry: LeaguePoolEntry) -> bool:
    comp_name = str(
        raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or ""
    )
    comp_country = raw.get("competition_country")
    pool_entry = resolve_pool_entry(comp_name, str(comp_country) if comp_country else None)
    return pool_entry is not None and pool_entry.key == entry.key


def _discovered_to_raw(fixture) -> dict:
    raw = dict(fixture.raw or {})
    raw.setdefault("match_id", fixture.match_id)
    raw.setdefault("home_team_name", fixture.home_team)
    raw.setdefault("away_team_name", fixture.away_team)
    raw.setdefault("source_url", fixture.match_url)
    raw.setdefault("url", fixture.match_url)
    raw.setdefault("kickoff_utc", fixture.kickoff_utc)
    raw.setdefault("date", fixture.match_date)
    raw.setdefault("status", fixture.status)
    if fixture.competition_name:
        raw.setdefault("competition_name", fixture.competition_name)
    if fixture.competition_country:
        raw.setdefault("competition_country", fixture.competition_country)
    raw["_discovery_source"] = True
    return raw


def discovery_query_for_pool_entry(entry: LeaguePoolEntry) -> str:
    if entry.countries:
        return f"{entry.display_name} {entry.countries[0].title()}"
    return entry.display_name


def fetch_fixtures_for_pool_entry(
    entry: LeaguePoolEntry,
    date_str: str,
    raw_list: Sequence[dict],
    *,
    use_discovery_fallback: bool,
    resolver: Optional[CompetitionResolverService] = None,
    fixture_svc: Optional[FixtureDiscoveryService] = None,
) -> Tuple[List[dict], List[str]]:
    """
    Return fixture raw dicts for one pool entry on one date.

    1. Filter ``raw_list`` (list-by-date path).
    2. If empty and fallback enabled → FixtureDiscoveryService.
    """
    warnings: List[str] = []
    from_list = [r for r in raw_list if _raw_matches_pool_entry(r, entry)]
    if from_list:
        return from_list, warnings

    if not use_discovery_fallback:
        return [], warnings

    resolver = resolver or CompetitionResolverService()
    fixture_svc = fixture_svc or FixtureDiscoveryService(resolver=resolver)
    query = discovery_query_for_pool_entry(entry)

    try:
        resolve = resolver.resolve_competition(query)
    except Exception as exc:
        logger.warning("discovery resolve failed entry=%s: %s", entry.key, exc)
        warnings.append(f"discovery_resolve_error:{entry.key}")
        return [], warnings

    if resolve.resolved is None:
        warnings.append(f"discovery_unresolved:{entry.key}")
        if resolve.ambiguous and resolve.candidates:
            warnings.append(f"discovery_ambiguous:{entry.key}")
        return [], warnings

    try:
        discovered = fixture_svc.list_competition_fixtures(
            resolve.resolved,
            date_from=date_str,
            date_to=date_str,
        )
    except Exception as exc:
        logger.warning("discovery fixtures failed entry=%s: %s", entry.key, exc)
        warnings.append(f"discovery_fixtures_error:{entry.key}")
        return [], warnings

    warnings.extend(discovered.warnings)
    if not discovered.fixtures:
        warnings.append(f"discovery_empty:{entry.key}:{date_str}")
        return [], warnings

    warnings.append(f"discovery_fallback_used:{entry.key}")
    return [_discovered_to_raw(f) for f in discovered.fixtures], warnings
