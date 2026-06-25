"""
Batch settlement for league eval-pool runs from Flashscore finished matches.

Writes final scores into ``match_results`` (shared with offline evaluation).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from football_agent.eval_pool.scope import filter_pool_keys, resolve_pool_entry
from football_agent.storage.v2_database import V2Database

logger = logging.getLogger(__name__)

_FINISHED_STATUSES = frozenset(
    {
        "finished",
        "ft",
        "ended",
        "aet",
        "pen",
        "after pen.",
        "after penalties",
        "after extra time",
        "full-time",
        "full time",
        "fulltime",
        "match finished",
        "complete",
        "completed",
        "final",
    }
)

_NOT_FINISHED_STATUSES = frozenset(
    {
        "scheduled",
        "not started",
        "postponed",
        "cancelled",
        "canceled",
        "abandoned",
        "walkover",
        "awarded",
        "delayed",
        "interrupted",
        "live",
        "1h",
        "2h",
        "ht",
        "halftime",
        "half time",
    }
)


def is_finished_status(status: Optional[str]) -> bool:
    s = (status or "").strip().lower()
    if not s:
        return False
    if s in _NOT_FINISHED_STATUSES:
        return False
    if s in _FINISHED_STATUSES:
        return True
    if s.startswith("finished"):
        return True
    # Common Flashscore short codes (after lowercasing)
    if s in ("ft", "aet", "pen"):
        return True
    return False


def classify_result_resolution(raw: dict) -> str:
    """
    Deterministic classification of a scraper list/detail payload for diagnostics.

    Does not guess — only reflects explicit status + score fields.
    """
    status = str(raw.get("status") or raw.get("state") or "").strip()
    score = extract_final_score(raw)
    if is_finished_status(status) and score is not None:
        return "finished_with_score"
    if is_finished_status(status) and score is None:
        return "finished_missing_score"
    if score is not None and not is_finished_status(status):
        return "score_present_status_not_finished"
    sl = status.lower()
    if not sl:
        return "missing_status"
    if sl in _NOT_FINISHED_STATUSES:
        return "not_finished"
    if is_finished_status(status):
        return "finished_missing_score"
    return "status_unrecognized"


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    m = re.match(r"^(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def extract_final_score(raw: dict) -> Optional[Tuple[int, int]]:
    """Best-effort score extraction from Flashscore raw list/detail payloads."""
    hs = _parse_int(raw.get("home_score"))
    aw = _parse_int(raw.get("away_score"))
    if hs is None:
        hs = _parse_int(raw.get("home_goals"))
    if aw is None:
        aw = _parse_int(raw.get("away_goals"))

    score_block = raw.get("score")
    if isinstance(score_block, dict):
        if hs is None and score_block.get("home") is not None:
            hs = _parse_int(score_block.get("home"))
        if hs is None and score_block.get("home_score") is not None:
            hs = _parse_int(score_block.get("home_score"))
        if aw is None and score_block.get("away") is not None:
            aw = _parse_int(score_block.get("away"))
        if aw is None and score_block.get("away_score") is not None:
            aw = _parse_int(score_block.get("away_score"))

    result_block = raw.get("result")
    if isinstance(result_block, dict):
        if hs is None:
            hs = _parse_int(result_block.get("home") or result_block.get("home_score"))
        if aw is None:
            aw = _parse_int(result_block.get("away") or result_block.get("away_score"))

    if hs is None or aw is None:
        return None
    return hs, aw


def _parse_date_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except ValueError:
        return None


_FLASHSCORE_TIME_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.")


def date_from_flashscore_display_time(
    time_val: Any,
    *,
    reference_year: int,
) -> Optional[str]:
    """
    Parse Flashscore fixture list ``time`` values like ``20.06. 14:30``.

    The scraper often leaves ``date``/``kickoff_utc`` null while embedding
    day/month in ``time``; year comes from the wave/discovery query context.
    """
    if time_val is None:
        return None
    m = _FLASHSCORE_TIME_DATE_RE.match(str(time_val).strip())
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{reference_year}-{month:02d}-{day:02d}"


def _reference_year_from_raw(raw: dict, *, reference_year: Optional[int] = None) -> Optional[int]:
    if reference_year is not None:
        return reference_year
    explicit = raw.get("_discovery_reference_year")
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    dfrom = raw.get("_discovery_date_from")
    if dfrom and len(str(dfrom)) >= 4:
        try:
            return int(str(dfrom)[:4])
        except ValueError:
            return None
    return None


def kickoff_date_from_raw(
    raw: dict,
    *,
    fallback_date: Optional[str] = None,
    reference_year: Optional[int] = None,
) -> Optional[str]:
    for key in ("kickoff_utc", "date", "match_date"):
        parsed = _parse_date_token(raw.get(key))
        if parsed:
            return parsed
    year = _reference_year_from_raw(raw, reference_year=reference_year)
    if year is not None:
        for time_key in ("time", "time_raw"):
            parsed = date_from_flashscore_display_time(raw.get(time_key), reference_year=year)
            if parsed:
                return parsed
    return fallback_date


def settle_league_pool_from_flashscore(
    *,
    date_from: str,
    date_to: str,
    league_keys: Optional[Sequence[str]] = None,
    db_path: str | Path | None = None,
    scraper_url: Optional[str] = None,
    fetch_matches_for_date: Optional[Callable[[str], List[dict]]] = None,
) -> Dict[str, Any]:
    """
    For each date, load Flashscore matches, filter wave-1 pool + finished status,
    persist scores into ``match_results``.
    """
    pool_entries = filter_pool_keys(league_keys)
    keys = tuple(e.key for e in pool_entries)
    summary: Dict[str, Any] = {
        "pipeline": "league_eval_pool_settle",
        "date_from": date_from,
        "date_to": date_to,
        "league_keys": list(keys),
        "fixtures_scanned": 0,
        "fixtures_in_scope": 0,
        "finished_in_scope": 0,
        "results_saved": 0,
        "skipped_no_score": 0,
        "skipped_not_finished": 0,
        "out_of_scope_skipped": 0,
        "saved": [],
        "errors": [],
    }

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date_to must be >= date_from")

    if fetch_matches_for_date is None:
        from football_agent.collectors.flashscore.client import FlashscoreCollectorClient
        from football_agent import config

        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        if not base:
            raise ValueError("FLASHSCORE_SCRAPER_URL not configured")
        client = FlashscoreCollectorClient(
            base,
            api_key=config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        fetch_matches_for_date = client.fetch_matches_for_date_raw

    db = V2Database(db_path)
    try:
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                raw_list = fetch_matches_for_date(date_str)
            except Exception as exc:
                logger.exception("settle fetch failed date=%s", date_str)
                summary["errors"].append({"date": date_str, "error": str(exc)})
                current += timedelta(days=1)
                continue

            summary["fixtures_scanned"] += len(raw_list)
            for raw in raw_list:
                comp_name = str(
                    raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or ""
                )
                comp_country = raw.get("competition_country")
                pool_entry = resolve_pool_entry(comp_name, str(comp_country) if comp_country else None)
                if pool_entry is None or pool_entry.key not in keys:
                    summary["out_of_scope_skipped"] += 1
                    continue

                summary["fixtures_in_scope"] += 1
                status = str(raw.get("status") or "")
                if not is_finished_status(status):
                    summary["skipped_not_finished"] += 1
                    continue

                summary["finished_in_scope"] += 1
                score = extract_final_score(raw)
                if score is None:
                    summary["skipped_no_score"] += 1
                    continue

                home = str(raw.get("home_team_name") or raw.get("home") or "").strip()
                away = str(raw.get("away_team_name") or raw.get("away") or "").strip()
                match_date = kickoff_date_from_raw(raw, fallback_date=date_str)
                if not home or not away or not match_date:
                    summary["skipped_no_score"] += 1
                    continue

                hs, aw = score
                db.save_match_result(match_date, home, away, hs, aw)
                summary["results_saved"] += 1
                summary["saved"].append(
                    {
                        "match_date": match_date,
                        "pool_key": pool_entry.key,
                        "home": home,
                        "away": away,
                        "home_score": hs,
                        "away_score": aw,
                    }
                )

            current += timedelta(days=1)
    finally:
        db.close()

    logger.info("league eval pool settlement done: %s", summary)
    return summary


def _match_url_from_raw(raw: dict) -> Optional[str]:
    url = str(raw.get("source_url") or raw.get("url") or "").strip()
    if url:
        return url
    match_id = str(raw.get("match_id") or raw.get("id") or "").strip()
    if match_id:
        return f"https://www.flashscore.com/match/football/x/x/?mid={match_id}"
    return None


def _fixture_dedupe_key(raw: dict) -> Tuple[str, str, str]:
    home = str(raw.get("home_team_name") or raw.get("home") or "")
    away = str(raw.get("away_team_name") or raw.get("away") or "")
    mid = str(raw.get("match_id") or raw.get("id") or "")
    return (mid or f"{home}:{away}", home, away)


def _try_resolve_finished_score(
    raw: dict,
    *,
    client: Any = None,
    enrich_match_detail: bool = True,
    fallback_date: Optional[str] = None,
) -> Tuple[Optional[Tuple[int, int]], dict, Dict[str, Any]]:
    """Return (score, effective_raw, probe_meta) from list payload or match-detail enrichment."""
    meta: Dict[str, Any] = {
        "list_resolution": classify_result_resolution(raw),
        "detail_attempted": False,
        "detail_resolution": None,
        "detail_error": None,
    }
    effective = raw
    if is_finished_status(str(raw.get("status") or "")):
        score = extract_final_score(raw)
        if score:
            meta["resolved_via"] = "list"
            return score, effective, meta

    if not enrich_match_detail or client is None:
        return None, effective, meta

    url = _match_url_from_raw(raw)
    if not url:
        meta["detail_error"] = "missing_match_url"
        return None, effective, meta

    meta["detail_attempted"] = True
    try:
        detail = client.fetch_match_raw_enriched(url)
        effective = detail
        meta["detail_resolution"] = classify_result_resolution(detail)
        if is_finished_status(str(detail.get("status") or "")):
            score = extract_final_score(detail)
            if score:
                meta["resolved_via"] = "detail"
                return score, effective, meta
    except Exception as exc:
        logger.debug("match detail enrich failed url=%s: %s", url, exc)
        meta["detail_error"] = str(exc)
    return None, effective, meta


def _persist_finished_fixture(
    db: V2Database,
    raw: dict,
    *,
    pool_key: str,
    fallback_date: Optional[str] = None,
) -> Optional[dict]:
    home = str(raw.get("home_team_name") or raw.get("home") or "").strip()
    away = str(raw.get("away_team_name") or raw.get("away") or "").strip()
    match_date = kickoff_date_from_raw(
        raw,
        fallback_date=fallback_date,
        reference_year=_reference_year_from_raw(raw),
    )
    if not home or not away or not match_date:
        return None

    score = extract_final_score(raw)
    if score is None:
        return None

    hs, aw = score
    db.save_match_result(match_date, home, away, hs, aw)
    return {
        "match_date": match_date,
        "pool_key": pool_key,
        "home": home,
        "away": away,
        "home_score": hs,
        "away_score": aw,
    }


def _empty_result_source_diagnostics() -> Dict[str, Any]:
    return {
        "pools_checked": 0,
        "fixtures_returned": 0,
        "in_range_fixtures": 0,
        "results_endpoint_rows_returned": 0,
        "results_endpoint_in_range": 0,
        "results_endpoint_finished": 0,
        "results_endpoint_empty": 0,
        "results_endpoint_error": 0,
        "results_detail_enriched": 0,
        "finished_list_recognized": 0,
        "scored_list_recognized": 0,
        "detail_probes_attempted": 0,
        "detail_finished_confirmed": 0,
        "detail_failed": 0,
        "run_identity_candidates": 0,
        "run_identity_detail_probes": 0,
        "results_persisted": 0,
        "results_saved": 0,
        "primary_blocker": None,
        "blocker_histogram": {},
        "results_endpoint": "/v1/competitions/results",
    }


def _find_raw_for_identity(
    raw_fixtures: Sequence[dict],
    *,
    match_date: str,
    home_team: str,
    away_team: str,
    reference_year: int,
) -> Optional[dict]:
    """Deterministic team+date match against competition fixture list (exact normalized names)."""
    from football_agent.offline.evaluation_v2 import normalize_team_for_settlement

    th = normalize_team_for_settlement(home_team)
    ta = normalize_team_for_settlement(away_team)
    for raw in raw_fixtures:
        fd = kickoff_date_from_raw(raw, reference_year=reference_year)
        if fd != match_date:
            continue
        rh = normalize_team_for_settlement(str(raw.get("home_team_name") or raw.get("home") or ""))
        ra = normalize_team_for_settlement(str(raw.get("away_team_name") or raw.get("away") or ""))
        if rh == th and ra == ta:
            return raw
    return None


def settle_league_pool_with_discovery(
    *,
    date_from: str,
    date_to: str,
    league_keys: Optional[Sequence[str]] = None,
    db_path: str | Path | None = None,
    scraper_url: Optional[str] = None,
    fetch_matches_for_date: Optional[Callable[[str], List[dict]]] = None,
    fetch_fixtures_for_entry_fn: Optional[Callable] = None,
    fetch_results_for_entry_fn: Optional[Callable] = None,
    use_discovery_fallback: bool = True,
    enrich_match_detail: bool = True,
    settle_from_saved_identities: bool = True,
    saved_identity_rows: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """
    Settlement via per-pool competition discovery + ``/v1/competitions/results``.

    Fetches finished matches from the scraper results endpoint (not fixtures/upcoming).
    Optionally re-probes saved run identities against the per-pool results list.
    """
    from football_agent.eval_pool.fixture_date import filter_fixtures_in_date_range, reference_year_for_wave
    from football_agent.eval_pool.fixture_sources import _discovered_to_raw
    from football_agent.eval_pool.result_fetch_probe import (
        SCRAPER_BLOCKER_DETAIL_FAILED,
        SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED,
        SCRAPER_BLOCKER_RESULTS_EMPTY,
        SCRAPER_BLOCKER_RESULTS_ERROR,
        SCRAPER_BLOCKER_SAVED,
    )

    pool_entries = filter_pool_keys(league_keys)
    keys = tuple(e.key for e in pool_entries)
    ref_year = reference_year_for_wave(date_from)
    result_source = _empty_result_source_diagnostics()
    blocker_hist: Dict[str, int] = {}

    summary: Dict[str, Any] = {
        "pipeline": "league_eval_pool_settle_discovery",
        "date_from": date_from,
        "date_to": date_to,
        "league_keys": list(keys),
        "use_discovery_fallback": use_discovery_fallback,
        "results_endpoint": "/v1/competitions/results",
        "fixtures_scanned": 0,
        "fixtures_seen_total": 0,
        "fixtures_in_range": 0,
        "fixtures_in_scope": 0,
        "finished_in_scope": 0,
        "results_saved": 0,
        "skipped_no_score": 0,
        "skipped_not_finished": 0,
        "skipped_status_unrecognized": 0,
        "detail_enriched": 0,
        "detail_probes_attempted": 0,
        "detail_failed": 0,
        "run_identity_probes": 0,
        "out_of_scope_skipped": 0,
        "discovery_warnings": [],
        "result_source_diagnostics": result_source,
        "saved": [],
        "errors": [],
    }

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date_to must be >= date_from")

    client = None
    if enrich_match_detail:
        from football_agent.collectors.flashscore.client import FlashscoreCollectorClient
        from football_agent import config

        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        if not base:
            raise ValueError("FLASHSCORE_SCRAPER_URL not configured")
        client = FlashscoreCollectorClient(
            base,
            api_key=config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        if fetch_matches_for_date is None:
            fetch_matches_for_date = client.fetch_matches_for_date_raw

    entry_fetch = fetch_fixtures_for_entry_fn  # legacy inject hook (unused for results path)
    db = V2Database(db_path)
    processed: set[Tuple[str, str, str, str]] = set()
    pool_raw_cache: Dict[str, List[dict]] = {}

    try:
        result_source["pools_checked"] = len(pool_entries)

        from football_agent.discovery.competition_resolver import CompetitionResolverService
        from football_agent.discovery.fixture_discovery import FixtureDiscoveryService

        resolver = CompetitionResolverService(scraper_url=scraper_url)
        fixture_svc = FixtureDiscoveryService(resolver=resolver, scraper_url=scraper_url)

        for entry in pool_entries:
            try:
                resolve = resolver.resolve_competition_for_pool_entry(entry)
            except AttributeError:
                from football_agent.eval_pool.fixture_sources import discovery_query_for_pool_entry

                resolve = resolver.resolve_competition(discovery_query_for_pool_entry(entry))

            if resolve.resolved is None:
                summary["discovery_warnings"].append(f"competition_unresolved:{entry.key}")
                result_source["results_endpoint_empty"] += 1
                blocker_hist["competition_unresolved"] = blocker_hist.get("competition_unresolved", 0) + 1
                continue

            competition_raws: List[dict] = []
            try:
                if fetch_results_for_entry_fn is not None:
                    competition_raws = list(
                        fetch_results_for_entry_fn(entry, date_from, date_to, use_discovery_fallback)
                        or [],
                    )
                else:
                    discovered = fixture_svc.list_competition_results(
                        resolve.resolved,
                        date_from=date_from,
                        date_to=date_to,
                    )
                    for w in discovered.warnings:
                        summary["discovery_warnings"].append(w)
                    competition_raws = [
                        _discovered_to_raw(f, discovery_date_from=date_from, discovery_date_to=date_to)
                        for f in discovered.fixtures
                    ]
            except Exception as exc:
                logger.warning("settle results fetch failed entry=%s: %s", entry.key, exc)
                summary["errors"].append({"pool_key": entry.key, "error": str(exc)})
                result_source["results_endpoint_error"] += 1
                blocker_hist[SCRAPER_BLOCKER_RESULTS_ERROR] = (
                    blocker_hist.get(SCRAPER_BLOCKER_RESULTS_ERROR, 0) + 1
                )
                continue

            rows_returned = len(competition_raws)
            result_source["results_endpoint_rows_returned"] += rows_returned
            result_source["fixtures_returned"] += rows_returned
            summary["fixtures_seen_total"] += rows_returned
            pool_raw_cache[entry.key] = competition_raws

            if not competition_raws:
                result_source["results_endpoint_empty"] += 1
                blocker_hist[SCRAPER_BLOCKER_RESULTS_EMPTY] = (
                    blocker_hist.get(SCRAPER_BLOCKER_RESULTS_EMPTY, 0) + 1
                )
                continue

            in_range, _skipped = filter_fixtures_in_date_range(
                competition_raws,
                date_from,
                date_to,
                allow_loop_date_fallback=False,
            )
            result_source["results_endpoint_in_range"] += len(in_range)
            result_source["in_range_fixtures"] += len(in_range)

            for raw in in_range:
                dedupe = _fixture_dedupe_key(raw)
                fixture_date = kickoff_date_from_raw(raw, reference_year=ref_year) or date_from
                proc_key = (fixture_date, entry.key, dedupe[0], dedupe[1])
                if proc_key in processed:
                    continue
                processed.add(proc_key)

                summary["fixtures_in_scope"] += 1
                summary["fixtures_in_range"] += 1

                list_res = classify_result_resolution(raw)
                if is_finished_status(str(raw.get("status") or "")):
                    result_source["finished_list_recognized"] += 1
                    result_source["results_endpoint_finished"] += 1
                if extract_final_score(raw):
                    result_source["scored_list_recognized"] += 1

                score, effective_raw, probe_meta = _try_resolve_finished_score(
                    raw,
                    client=client,
                    enrich_match_detail=enrich_match_detail,
                )
                if probe_meta.get("detail_attempted"):
                    summary["detail_probes_attempted"] += 1
                    result_source["detail_probes_attempted"] += 1
                if probe_meta.get("resolved_via") == "detail":
                    summary["detail_enriched"] += 1
                    result_source["detail_finished_confirmed"] += 1
                    result_source["results_detail_enriched"] += 1
                elif probe_meta.get("detail_attempted") and not score:
                    summary["detail_failed"] += 1
                    result_source["detail_failed"] += 1

                if score is None:
                    if list_res == "status_unrecognized":
                        summary["skipped_status_unrecognized"] += 1
                        blocker_hist["scraper_status_unrecognized"] = (
                            blocker_hist.get("scraper_status_unrecognized", 0) + 1
                        )
                    elif is_finished_status(str(effective_raw.get("status") or "")):
                        summary["skipped_no_score"] += 1
                        blocker_hist["scraper_missing_scores"] = (
                            blocker_hist.get("scraper_missing_scores", 0) + 1
                        )
                    else:
                        summary["skipped_not_finished"] += 1
                        blocker_hist[SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED] = (
                            blocker_hist.get(SCRAPER_BLOCKER_IN_RANGE_NOT_FINISHED, 0) + 1
                        )
                    continue

                summary["finished_in_scope"] += 1
                saved = _persist_finished_fixture(
                    db,
                    effective_raw,
                    pool_key=entry.key,
                    fallback_date=fixture_date,
                )
                if saved is None:
                    summary["skipped_no_score"] += 1
                    continue

                summary["results_saved"] += 1
                result_source["results_persisted"] += 1
                result_source["results_saved"] += 1
                summary["saved"].append(saved)
                blocker_hist[SCRAPER_BLOCKER_SAVED] = blocker_hist.get(SCRAPER_BLOCKER_SAVED, 0) + 1

        if settle_from_saved_identities and saved_identity_rows:
            for item in saved_identity_rows:
                pool_key = str(item.get("pool_key") or "")
                match_date = str(item.get("match_date") or "")
                home = str(item.get("home_team") or "")
                away = str(item.get("away_team") or "")
                if not pool_key or not match_date or not home or not away:
                    continue
                if pool_key not in pool_raw_cache:
                    continue
                existing = db.conn.execute(
                    "SELECT 1 FROM match_results WHERE match_date=? AND home_team=? AND away_team=?",
                    (match_date, home, away),
                ).fetchone()
                if existing:
                    continue

                raw = _find_raw_for_identity(
                    pool_raw_cache[pool_key],
                    match_date=match_date,
                    home_team=home,
                    away_team=away,
                    reference_year=ref_year,
                )
                if raw is None:
                    continue

                result_source["run_identity_candidates"] += 1
                summary["run_identity_probes"] += 1
                score, effective_raw, probe_meta = _try_resolve_finished_score(
                    raw,
                    client=client,
                    enrich_match_detail=enrich_match_detail,
                )
                result_source["run_identity_detail_probes"] += 1
                if probe_meta.get("detail_attempted"):
                    summary["detail_probes_attempted"] += 1
                    result_source["detail_probes_attempted"] += 1
                if score is None:
                    if probe_meta.get("detail_attempted"):
                        summary["detail_failed"] += 1
                        result_source["detail_failed"] += 1
                        blocker_hist[SCRAPER_BLOCKER_DETAIL_FAILED] = (
                            blocker_hist.get(SCRAPER_BLOCKER_DETAIL_FAILED, 0) + 1
                        )
                    continue

                saved = _persist_finished_fixture(
                    db,
                    effective_raw,
                    pool_key=pool_key,
                    fallback_date=match_date,
                )
                if saved:
                    summary["results_saved"] += 1
                    result_source["results_persisted"] += 1
                    result_source["results_saved"] += 1
                    summary["saved"].append(saved)

        result_source["blocker_histogram"] = blocker_hist
        if blocker_hist:
            result_source["primary_blocker"] = max(blocker_hist.items(), key=lambda x: x[1])[0]
        summary["result_source_diagnostics"] = result_source

    finally:
        db.close()

    logger.info("league eval pool discovery settlement done: %s", summary)
    return summary


def collect_saved_settlement_identities(
    *,
    league_keys: Sequence[str],
    date_from: str,
    date_to: str,
    db_path: str | Path | None = None,
) -> List[dict]:
    """Load persisted wave run identities for run-centric result fetch."""
    from football_agent.eval_pool.report import _in_pool_scope, _snapshot_meta
    from football_agent.offline.evaluation_v2 import extract_settlement_identity
    from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2

    allowed = tuple(league_keys)
    repo = EvaluationRepositoryV2(db_path=db_path)
    rows: List[dict] = []
    try:
        for row in repo.iter_scored_runs(
            date_from=date_from,
            date_to=f"{date_to}T23:59:59",
            limit=50000,
        ):
            snap = row.snapshot_json or {}
            meta = _snapshot_meta(snap)
            comp_name = meta.get("competition_name") or row.competition_code
            comp_country = meta.get("country")
            if not _in_pool_scope(
                competition_name=str(comp_name) if comp_name else None,
                competition_country=str(comp_country) if comp_country else None,
                allowed_keys=allowed,
            ):
                continue
            entry = resolve_pool_entry(
                str(comp_name) if comp_name else None,
                str(comp_country) if comp_country else None,
            )
            pool_key = entry.key if entry else "unknown"
            identity = extract_settlement_identity(
                snapshot_json=snap,
                run_home_team=row.home_team,
                run_away_team=row.away_team,
                run_kickoff_utc=row.kickoff_utc,
            )
            if identity is None:
                continue
            rows.append(
                {
                    "run_id": row.run_id,
                    "pool_key": pool_key,
                    "match_date": identity.match_date,
                    "home_team": identity.home_team,
                    "away_team": identity.away_team,
                }
            )
    finally:
        repo.close()
    return rows


def settle_league_pool(
    *,
    date_from: str,
    date_to: str,
    league_keys: Optional[Sequence[str]] = None,
    db_path: str | Path | None = None,
    scraper_url: Optional[str] = None,
    use_discovery_fallback: Optional[bool] = None,
    fetch_matches_for_date: Optional[Callable[[str], List[dict]]] = None,
    fetch_fixtures_for_entry_fn: Optional[Callable] = None,
    fetch_results_for_entry_fn: Optional[Callable] = None,
    enrich_match_detail: bool = True,
    saved_identity_rows: Optional[Sequence[dict]] = None,
    settle_from_saved_identities: bool = True,
) -> Dict[str, Any]:
    """
    Default settlement entrypoint for eval waves.

    Uses per-pool ``/v1/competitions/results`` when discovery fallback is enabled.
    """
    from football_agent import config

    fallback = (
        use_discovery_fallback
        if use_discovery_fallback is not None
        else config.EVAL_POOL_DISCOVERY_FALLBACK
    )
    if fallback:
        return settle_league_pool_with_discovery(
            date_from=date_from,
            date_to=date_to,
            league_keys=league_keys,
            db_path=db_path,
            scraper_url=scraper_url,
            fetch_matches_for_date=fetch_matches_for_date,
            fetch_fixtures_for_entry_fn=fetch_fixtures_for_entry_fn,
            fetch_results_for_entry_fn=fetch_results_for_entry_fn,
            use_discovery_fallback=True,
            enrich_match_detail=enrich_match_detail,
            saved_identity_rows=saved_identity_rows,
            settle_from_saved_identities=settle_from_saved_identities,
        )
    return settle_league_pool_from_flashscore(
        date_from=date_from,
        date_to=date_to,
        league_keys=league_keys,
        db_path=db_path,
        scraper_url=scraper_url,
        fetch_matches_for_date=fetch_matches_for_date,
    )
