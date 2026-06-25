"""
Read-only probes for Flashscore result-fetch / settlement source path.

Documents what the scraper returns vs what football_agent needs for ``match_results``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.eval_pool.fixture_date import extract_fixture_date, filter_fixtures_in_date_range
from football_agent.eval_pool.fixture_sources import _discovered_to_raw, discovery_query_for_pool_entry
from football_agent.eval_pool.scope import LeaguePoolEntry, filter_pool_keys
from football_agent.eval_pool.settle import (
    classify_result_resolution,
    extract_final_score,
    is_finished_status,
    kickoff_date_from_raw,
)

SCRAper_BLOCKER_EMPTY = "scraper_empty_response"
SCRAPER_BLOCKER_EMPTY = SCRAper_BLOCKER_EMPTY
SCRAPER_BLOCKER_FUTURE_ONLY = "scraper_returned_only_future_fixtures"
SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED = "scraper_returned_in_range_but_not_finished"
SCRAPER_BLOCKER_STATUS_UNRECOGNIZED = "scraper_status_unrecognized"
SCRAPER_BLOCKER_MISSING_SCORES = "scraper_missing_scores"
SCRAPER_BLOCKER_DETAIL_REQUIRED = "detail_fetch_required"
SCRAPER_BLOCKER_DETAIL_FAILED = "detail_fetch_failed"
SCRAPER_BLOCKER_SAVED = "finished_results_found_and_saved"
SCRAPER_BLOCKER_RESULTS_EMPTY = "results_endpoint_empty"
SCRAPER_BLOCKER_RESULTS_ERROR = "results_endpoint_error"


@dataclass
class FixtureProbeRow:
    home: str
    away: str
    kickoff_date: Optional[str]
    display_time: Optional[str]
    status: str
    has_score: bool
    finished_recognized: bool
    source_url: Optional[str]
    resolution: str


@dataclass
class PoolResultFetchProbe:
    pool_key: str
    competition_name: str
    competition_url: Optional[str]
    endpoint: str
    request_params: Dict[str, str]
    fixtures_returned: int = 0
    in_range_count: int = 0
    finished_list_count: int = 0
    scored_list_count: int = 0
    earliest_kickoff: Optional[str] = None
    latest_kickoff: Optional[str] = None
    status_histogram: Dict[str, int] = field(default_factory=dict)
    resolution_histogram: Dict[str, int] = field(default_factory=dict)
    detail_probes_attempted: int = 0
    detail_finished_confirmed: int = 0
    detail_failed: int = 0
    examples_in_range: List[FixtureProbeRow] = field(default_factory=list)
    examples_problematic: List[FixtureProbeRow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    primary_blocker: Optional[str] = None
    blocker_detail: Optional[str] = None


def _fixture_probe_row(raw: dict, *, reference_year: int) -> FixtureProbeRow:
    home = str(raw.get("home_team_name") or raw.get("home") or "")
    away = str(raw.get("away_team_name") or raw.get("away") or "")
    kickoff = kickoff_date_from_raw(raw, reference_year=reference_year)
    status = str(raw.get("status") or "")
    score = extract_final_score(raw)
    resolution = classify_result_resolution(raw)
    return FixtureProbeRow(
        home=home,
        away=away,
        kickoff_date=kickoff,
        display_time=str(raw.get("time") or raw.get("time_raw") or "") or None,
        status=status,
        has_score=score is not None,
        finished_recognized=is_finished_status(status),
        source_url=str(raw.get("source_url") or raw.get("url") or "") or None,
        resolution=resolution,
    )


def probe_competition_url_result_fetch(
    competition_url: str,
    *,
    date_from: str,
    date_to: str,
    pool_key: str = "custom",
    competition_name: str = "custom",
    scraper_url: Optional[str] = None,
    detail_sample_limit: int = 5,
    enrich_client: Any = None,
) -> PoolResultFetchProbe:
    """Probe a known competition URL directly (bypasses pool resolver)."""
    from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient
    from football_agent.eval_pool.fixture_sources import _discovered_to_raw
    from football_agent.discovery.models import (
        CompetitionCandidate,
        DiscoveredFixture,
        ResolvedCompetition,
    )

    probe = PoolResultFetchProbe(
        pool_key=pool_key,
        competition_name=competition_name,
        competition_url=competition_url,
        endpoint="/v1/competitions/results",
        request_params={
            "competition_url": competition_url,
            "date_from": date_from,
            "date_to": date_to,
        },
    )

    from football_agent import config

    base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
    if not base:
        probe.primary_blocker = SCRAPER_BLOCKER_EMPTY
        probe.blocker_detail = "FLASHSCORE_SCRAPER_URL not configured"
        return probe

    client = FlashscoreDiscoveryClient(
        base,
        api_key=config.FLASHSCORE_SCRAPER_API_KEY,
        timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
    )
    raw_list = client.fetch_competition_results(competition_url, date_from=date_from, date_to=date_to)
    probe.fixtures_returned = len(raw_list)
    if not raw_list:
        probe.primary_blocker = SCRAPER_BLOCKER_RESULTS_EMPTY
        probe.blocker_detail = "scraper /v1/competitions/results returned zero rows"
        return probe

    ref_year = int(date_from[:4])
    candidate = CompetitionCandidate(
        competition_name=competition_name,
        country=None,
        url=competition_url,
        source="probe",
        confidence="high",
    )
    resolved = ResolvedCompetition(candidate=candidate, ambiguous=False)
    fixtures = [
        DiscoveredFixture(
            match_id=str(r.get("match_id") or ""),
            match_url=str(r.get("source_url") or r.get("url") or ""),
            home_team=str(r.get("home_team_name") or r.get("home") or ""),
            away_team=str(r.get("away_team_name") or r.get("away") or ""),
            kickoff_utc=r.get("kickoff_utc"),
            match_date=kickoff_date_from_raw(r, reference_year=ref_year),
            status=str(r.get("status") or "scheduled"),
            competition_name=competition_name,
            raw=r,
        )
        for r in raw_list
        if r.get("home_team_name") or r.get("home")
    ]
    competition_raws = [
        _discovered_to_raw(f, discovery_date_from=date_from, discovery_date_to=date_to)
        for f in fixtures
    ]
    return _finalize_pool_probe(
        probe,
        competition_raws=competition_raws,
        date_from=date_from,
        date_to=date_to,
        ref_year=ref_year,
        detail_sample_limit=detail_sample_limit,
        enrich_client=enrich_client,
    )


def _finalize_pool_probe(
    probe: PoolResultFetchProbe,
    *,
    competition_raws: List[dict],
    date_from: str,
    date_to: str,
    ref_year: int,
    detail_sample_limit: int,
    enrich_client: Any,
) -> PoolResultFetchProbe:
    dates: List[str] = []
    resolution_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()

    for raw in competition_raws:
        row = _fixture_probe_row(raw, reference_year=ref_year)
        status_counts[row.status or "(empty)"] += 1
        resolution_counts[row.resolution] += 1
        if row.kickoff_date:
            dates.append(row.kickoff_date)

    in_range, _skipped = filter_fixtures_in_date_range(
        competition_raws,
        date_from,
        date_to,
        allow_loop_date_fallback=False,
    )
    probe.in_range_count = len(in_range)
    probe.finished_list_count = sum(
        1 for raw in in_range if is_finished_status(str(raw.get("status") or ""))
    )
    probe.scored_list_count = sum(1 for raw in in_range if extract_final_score(raw) is not None)

    if dates:
        probe.earliest_kickoff = min(dates)
        probe.latest_kickoff = max(dates)

    probe.status_histogram = dict(status_counts)
    probe.resolution_histogram = dict(resolution_counts)

    for raw in in_range[:detail_sample_limit]:
        probe.examples_in_range.append(_fixture_probe_row(raw, reference_year=ref_year))

    if probe.in_range_count == 0 and dates and probe.earliest_kickoff and probe.earliest_kickoff > date_to:
        probe.primary_blocker = SCRAPER_BLOCKER_FUTURE_ONLY
        probe.blocker_detail = (
            f"scraper returned {probe.fixtures_returned} fixtures but client-side in-range=0; "
            f"earliest parsed kickoff={probe.earliest_kickoff} > date_to={date_to}"
        )
        for raw in competition_raws[:3]:
            probe.examples_problematic.append(_fixture_probe_row(raw, reference_year=ref_year))
        return probe

    if probe.in_range_count == 0:
        probe.primary_blocker = SCRAPER_BLOCKER_EMPTY
        probe.blocker_detail = (
            f"scraper returned {probe.fixtures_returned} fixtures but none in [{date_from}, {date_to}]"
        )
        return probe

    if probe.finished_list_count == 0 and probe.scored_list_count == 0:
        probe.primary_blocker = SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED
        probe.blocker_detail = (
            f"{probe.in_range_count} in-range fixtures but list has no finished status/scores "
            f"(statuses: {probe.status_histogram})"
        )

    if enrich_client is not None and probe.in_range_count > 0:
        from football_agent.eval_pool.settle import _match_url_from_raw, _try_resolve_finished_score

        for raw in in_range[:detail_sample_limit]:
            probe.detail_probes_attempted += 1
            url = _match_url_from_raw(raw)
            if not url:
                probe.detail_failed += 1
                continue
            score, effective, _meta = _try_resolve_finished_score(
                raw,
                client=enrich_client,
                enrich_match_detail=True,
            )
            if score is not None:
                probe.detail_finished_confirmed += 1
                probe.primary_blocker = SCRAPER_BLOCKER_SAVED
                probe.blocker_detail = "detail enrichment confirmed finished score"
            else:
                probe.detail_failed += 1
                probe.examples_problematic.append(_fixture_probe_row(effective, reference_year=ref_year))

        if (
            probe.primary_blocker == SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED
            and probe.detail_probes_attempted > 0
            and probe.detail_finished_confirmed == 0
        ):
            probe.primary_blocker = SCRAPER_BLOCKER_DETAIL_REQUIRED
            probe.blocker_detail = (
                f"list not finished; {probe.detail_probes_attempted} detail probes, 0 confirmed"
            )

    return probe


def probe_pool_result_fetch(
    entry: LeaguePoolEntry,
    *,
    date_from: str,
    date_to: str,
    scraper_url: Optional[str] = None,
    detail_sample_limit: int = 5,
    enrich_client: Any = None,
) -> PoolResultFetchProbe:
    """
    Probe one pool: resolve competition → ``GET /v1/competitions/results`` → classify payloads.
    """
    resolver = CompetitionResolverService(scraper_url=scraper_url)
    fixture_svc = FixtureDiscoveryService(resolver=resolver, scraper_url=scraper_url)

    try:
        resolve = resolver.resolve_competition_for_pool_entry(entry)
    except AttributeError:
        resolve = resolver.resolve_competition(discovery_query_for_pool_entry(entry))

    comp_url = None
    if resolve.resolved is not None:
        comp_url = resolve.resolved.candidate.fixtures_url or resolve.resolved.candidate.url

    probe = PoolResultFetchProbe(
        pool_key=entry.key,
        competition_name=entry.display_name,
        competition_url=comp_url,
        endpoint="/v1/competitions/results",
        request_params={
            "competition_url": comp_url or "",
            "date_from": date_from,
            "date_to": date_to,
        },
    )

    if resolve.resolved is None:
        probe.warnings.append("competition_unresolved")
        probe.primary_blocker = SCRAper_BLOCKER_EMPTY
        probe.blocker_detail = "competition resolver returned no URL"
        return probe

    discovered = fixture_svc.list_competition_results(
        resolve.resolved,
        date_from=date_from,
        date_to=date_to,
    )
    probe.warnings.extend(discovered.warnings)

    ref_year = int(date_from[:4])
    raw_fixtures = [
        _discovered_to_raw(f, discovery_date_from=date_from, discovery_date_to=date_to)
        for f in discovered.fixtures
    ]
    probe.fixtures_returned = len(raw_fixtures)

    if not raw_fixtures:
        probe.primary_blocker = SCRAPER_BLOCKER_RESULTS_EMPTY
        probe.blocker_detail = "scraper /v1/competitions/results returned zero rows for pool"
        return probe

    return _finalize_pool_probe(
        probe,
        competition_raws=raw_fixtures,
        date_from=date_from,
        date_to=date_to,
        ref_year=ref_year,
        detail_sample_limit=detail_sample_limit,
        enrich_client=enrich_client,
    )


def probe_wave_result_fetch(
    *,
    league_keys: Sequence[str],
    date_from: str,
    date_to: str,
    scraper_url: Optional[str] = None,
    detail_sample_limit: int = 3,
) -> Dict[str, Any]:
    """Probe all pools in a wave date window."""
    enrich_client = None
    if detail_sample_limit > 0:
        from football_agent import config
        from football_agent.collectors.flashscore.client import FlashscoreCollectorClient

        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        if base:
            enrich_client = FlashscoreCollectorClient(
                base,
                api_key=config.FLASHSCORE_SCRAPER_API_KEY,
                timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
            )

    pools: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()

    for entry in filter_pool_keys(league_keys):
        probe = probe_pool_result_fetch(
            entry,
            date_from=date_from,
            date_to=date_to,
            scraper_url=scraper_url,
            detail_sample_limit=detail_sample_limit,
            enrich_client=enrich_client,
        )
        if probe.primary_blocker:
            blockers[probe.primary_blocker] += 1
        pools.append(_probe_to_dict(probe))

    total_in_range = sum(int(p.get("in_range_count") or 0) for p in pools)
    total_returned = sum(int(p.get("fixtures_returned") or 0) for p in pools)

    primary = blockers.most_common(1)[0][0] if blockers else None
    return {
        "pipeline": "result_fetch_probe",
        "date_from": date_from,
        "date_to": date_to,
        "league_keys": list(league_keys),
        "pools_checked": len(pools),
        "results_endpoint": "/v1/competitions/results",
        "fixtures_returned_total": total_returned,
        "results_endpoint_rows_returned_total": total_returned,
        "in_range_total": total_in_range,
        "results_endpoint_in_range_total": total_in_range,
        "blocker_histogram": dict(blockers),
        "primary_blocker": primary,
        "pools": pools,
        "scraper_limitations": _scraper_limitation_notes(pools, date_from, date_to),
    }


def _probe_to_dict(probe: PoolResultFetchProbe) -> Dict[str, Any]:
    def _rows(rows: List[FixtureProbeRow]) -> List[Dict[str, Any]]:
        return [
            {
                "home": r.home,
                "away": r.away,
                "kickoff_date": r.kickoff_date,
                "display_time": r.display_time,
                "status": r.status,
                "has_score": r.has_score,
                "finished_recognized": r.finished_recognized,
                "resolution": r.resolution,
                "source_url": r.source_url,
            }
            for r in rows
        ]

    return {
        "pool_key": probe.pool_key,
        "competition_name": probe.competition_name,
        "competition_url": probe.competition_url,
        "endpoint": probe.endpoint,
        "request_params": probe.request_params,
        "fixtures_returned": probe.fixtures_returned,
        "in_range_count": probe.in_range_count,
        "finished_list_count": probe.finished_list_count,
        "scored_list_count": probe.scored_list_count,
        "earliest_kickoff": probe.earliest_kickoff,
        "latest_kickoff": probe.latest_kickoff,
        "status_histogram": probe.status_histogram,
        "resolution_histogram": probe.resolution_histogram,
        "detail_probes_attempted": probe.detail_probes_attempted,
        "detail_finished_confirmed": probe.detail_finished_confirmed,
        "detail_failed": probe.detail_failed,
        "primary_blocker": probe.primary_blocker,
        "blocker_detail": probe.blocker_detail,
        "warnings": probe.warnings,
        "examples_in_range": _rows(probe.examples_in_range),
        "examples_problematic": _rows(probe.examples_problematic),
    }


def _scraper_limitation_notes(
    pools: List[Dict[str, Any]],
    date_from: str,
    date_to: str,
) -> List[str]:
    notes: List[str] = []
    empty_results = [
        p["pool_key"]
        for p in pools
        if p.get("primary_blocker") in (SCRAPER_BLOCKER_RESULTS_EMPTY, SCRAPER_BLOCKER_EMPTY)
    ]
    if empty_results:
        notes.append(
            "GET /v1/competitions/results returned zero in-range rows for pools: "
            f"{', '.join(empty_results[:5])}."
        )
    future_only = [
        p["pool_key"]
        for p in pools
        if p.get("primary_blocker") == SCRAPER_BLOCKER_FUTURE_ONLY
    ]
    if future_only:
        notes.append(
            "Legacy fixtures probe would be upcoming-only for pools: "
            f"{', '.join(future_only[:5])}. Settlement now uses /results."
        )
    if all(int(p.get("finished_list_count") or 0) == 0 for p in pools if int(p.get("in_range_count") or 0) > 0):
        if any(int(p.get("in_range_count") or 0) > 0 for p in pools):
            notes.append(
                "In-range fixtures exist but none have finished status or scores in list payloads."
            )
    all_scheduled = all(
        set((p.get("status_histogram") or {}).keys()) <= {"scheduled", "(empty)"}
        for p in pools
        if int(p.get("fixtures_returned") or 0) > 0
    )
    if all_scheduled and any(int(p.get("fixtures_returned") or 0) > 0 for p in pools):
        notes.append(
            "All scraper list statuses are 'scheduled' — unexpected on /v1/competitions/results; "
            "check scraper results path or match-detail enrichment."
        )
    detail_failed = sum(int(p.get("detail_probes_attempted") or 0) for p in pools)
    detail_ok = sum(int(p.get("detail_finished_confirmed") or 0) for p in pools)
    if detail_failed > 0 and detail_ok == 0:
        notes.append(
            "Match-detail enrichment probes did not return finished+score for in-range samples. "
            "Scraper /v1/match likely does not expose final results for these leagues yet."
        )
    if not notes:
        notes.append("No obvious scraper limitation pattern detected on this probe.")
    return notes


def format_probe_cli_summary(payload: Dict[str, Any]) -> str:
    lines = [
        f"Result fetch probe: {payload.get('date_from')} .. {payload.get('date_to')}",
        f"- endpoint: {payload.get('results_endpoint', '/v1/competitions/results')}",
        f"- pools checked: {payload.get('pools_checked')}",
        f"- results rows returned (total): {payload.get('results_endpoint_rows_returned_total', payload.get('fixtures_returned_total'))}",
        f"- in-range (client filter): {payload.get('in_range_total')}",
        f"- primary blocker: {payload.get('primary_blocker')}",
    ]
    for note in payload.get("scraper_limitations") or []:
        lines.append(f"- note: {note}")
    lines.append("")
    lines.append("Per pool:")
    for p in payload.get("pools") or []:
        lines.append(
            f"  • {p.get('pool_key')}: returned={p.get('fixtures_returned')} "
            f"in_range={p.get('in_range_count')} finished_list={p.get('finished_list_count')} "
            f"blocker={p.get('primary_blocker')}"
        )
        if p.get("blocker_detail"):
            lines.append(f"      {p['blocker_detail']}")
        hist = p.get("status_histogram") or {}
        if hist:
            lines.append(f"      statuses: {hist}")
        if p.get("earliest_kickoff"):
            lines.append(
                f"      kickoff span: {p.get('earliest_kickoff')} .. {p.get('latest_kickoff')}"
            )
    return "\n".join(lines)
