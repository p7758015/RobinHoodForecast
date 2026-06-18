"""Collect fixtures for eval-pool: list-by-date filter + optional discovery fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.eval_pool.fixture_date import filter_fixtures_in_date_range
from football_agent.eval_pool.scope import LeaguePoolEntry, resolve_pool_entry

logger = logging.getLogger(__name__)

# Scraper list-by-code paths (legacy); FS_* wave-1 entries skip this path.
_LEGACY_LIST_CODES = frozenset({"PL", "CL", "BL1", "SA", "PD", "FL1"})


@dataclass(frozen=True)
class FixtureFetchStats:
    seen: int = 0
    in_range: int = 0
    skipped_out_of_range: int = 0


@dataclass(frozen=True)
class FixtureFetchResult:
    """Stable return type for ``fetch_fixtures_for_pool_entry``."""

    fixtures: List[dict]
    warnings: List[str]
    stats: FixtureFetchStats

    @classmethod
    def empty(cls, warnings: Optional[List[str]] = None) -> "FixtureFetchResult":
        return cls(fixtures=[], warnings=list(warnings or []), stats=FixtureFetchStats())

    @classmethod
    def from_parts(
        cls,
        fixtures: List[dict],
        warnings: List[str],
        *,
        seen: int,
        in_range: int,
        skipped_out_of_range: int,
    ) -> "FixtureFetchResult":
        return cls(
            fixtures=fixtures,
            warnings=warnings,
            stats=FixtureFetchStats(
                seen=seen,
                in_range=in_range,
                skipped_out_of_range=skipped_out_of_range,
            ),
        )


def _raw_matches_pool_entry(raw: dict, entry: LeaguePoolEntry) -> bool:
    comp_name = str(
        raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or ""
    )
    comp_country = raw.get("competition_country")
    pool_entry = resolve_pool_entry(comp_name, str(comp_country) if comp_country else None)
    return pool_entry is not None and pool_entry.key == entry.key


def _discovered_to_raw(
    fixture,
    *,
    discovery_date_from: str,
    discovery_date_to: str,
) -> dict:
    raw = dict(fixture.raw or {})
    raw.setdefault("match_id", fixture.match_id)
    raw.setdefault("home_team_name", fixture.home_team)
    raw.setdefault("away_team_name", fixture.away_team)
    raw.setdefault("source_url", fixture.match_url)
    raw.setdefault("url", fixture.match_url)
    raw.setdefault("status", fixture.status)
    if fixture.competition_name:
        raw.setdefault("competition_name", fixture.competition_name)
    if fixture.competition_country:
        raw.setdefault("competition_country", fixture.competition_country)
    raw["_discovery_source"] = True
    raw["_discovery_date_from"] = discovery_date_from
    raw["_discovery_date_to"] = discovery_date_to
    raw["_discovery_reference_year"] = int(discovery_date_from[:4])
    if fixture.kickoff_utc:
        raw["kickoff_utc"] = fixture.kickoff_utc
    if fixture.match_date:
        raw["date"] = fixture.match_date
    from football_agent.eval_pool.fixture_date import extract_fixture_date

    resolved_date = extract_fixture_date(raw)
    if resolved_date:
        raw["date"] = resolved_date
    elif fixture.match_date:
        raw["date"] = fixture.match_date
    if not raw.get("kickoff_utc") and resolved_date and raw.get("time"):
        raw.setdefault("kickoff_utc", f"{resolved_date}T00:00:00+00:00")
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
    wave_date_from: Optional[str] = None,
    wave_date_to: Optional[str] = None,
    resolver: Optional[CompetitionResolverService] = None,
    fixture_svc: Optional[FixtureDiscoveryService] = None,
) -> FixtureFetchResult:
    """
    Return fixture raw dicts for one pool entry on one date.

    1. Filter ``raw_list`` (list-by-date path).
    2. If empty and fallback enabled → FixtureDiscoveryService.
    3. Final authoritative filter to wave date range.
    """
    range_from = wave_date_from or date_str
    range_to = wave_date_to or date_str
    warnings: List[str] = []
    from_list = [r for r in raw_list if _raw_matches_pool_entry(r, entry)]
    if from_list:
        filtered, skipped = filter_fixtures_in_date_range(
            from_list, range_from, range_to, loop_date=date_str,
        )
        if skipped:
            warnings.append(f"out_of_range_skipped:{entry.key}:{skipped}")
        return FixtureFetchResult.from_parts(
            filtered, warnings, seen=len(from_list), in_range=len(filtered), skipped_out_of_range=skipped,
        )

    if not use_discovery_fallback:
        return FixtureFetchResult.empty(warnings)

    resolver = resolver or CompetitionResolverService()
    fixture_svc = fixture_svc or FixtureDiscoveryService(resolver=resolver)

    try:
        resolve = resolver.resolve_competition_for_pool_entry(entry)
    except AttributeError:
        query = discovery_query_for_pool_entry(entry)
        resolve = resolver.resolve_competition(query)
    except Exception as exc:
        logger.warning("discovery resolve failed entry=%s: %s", entry.key, exc)
        warnings.append(f"discovery_resolve_error:{entry.key}")
        return FixtureFetchResult.empty(warnings)

    if resolve.resolved is None:
        warnings.append(f"discovery_unresolved:{entry.key}")
        if resolve.ambiguous and resolve.candidates:
            warnings.append(f"discovery_ambiguous:{entry.key}")
        return FixtureFetchResult.empty(warnings)

    try:
        discovered = fixture_svc.list_competition_fixtures(
            resolve.resolved,
            date_from=range_from,
            date_to=range_to,
        )
    except Exception as exc:
        logger.warning("discovery fixtures failed entry=%s: %s", entry.key, exc)
        warnings.append(f"discovery_fixtures_error:{entry.key}")
        return FixtureFetchResult.empty(warnings)

    warnings.extend(discovered.warnings)
    if not discovered.fixtures:
        warnings.append(f"discovery_empty:{entry.key}:{date_str}")
        return FixtureFetchResult.empty(warnings)

    warnings.append(f"discovery_fallback_used:{entry.key}")
    raw_fixtures = [
        _discovered_to_raw(f, discovery_date_from=range_from, discovery_date_to=range_to)
        for f in discovered.fixtures
    ]
    filtered, skipped = filter_fixtures_in_date_range(
        raw_fixtures,
        range_from,
        range_to,
        loop_date=date_str,
        allow_loop_date_fallback=False,
    )
    if skipped:
        warnings.append(f"discovery_out_of_range_skipped:{entry.key}:{skipped}")
    return FixtureFetchResult.from_parts(
        filtered,
        warnings,
        seen=len(raw_fixtures),
        in_range=len(filtered),
        skipped_out_of_range=skipped,
    )
