"""

Batch accumulation of league eval-pool runs via the live Flashscore pipeline.



Filters wave-1 competitions, scores league-eligible matches only, persists runs.

"""



from __future__ import annotations



import logging

from datetime import date, timedelta

from pathlib import Path

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple



from football_agent import config

from football_agent.eval_pool.fixture_sources import FixtureFetchResult, fetch_fixtures_for_pool_entry

from football_agent.eval_pool.fixture_date import evaluate_fixture_date_guard, is_discovery_fixture

from football_agent.eval_pool.scope import LOW_CONFIDENCE_THRESHOLD, LeaguePoolEntry, filter_pool_keys, resolve_pool_entry

from football_agent.flashscore.models import FlashscoreMeta

from football_agent.services.competition_classifier import classify_competition_meta

from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline, LivePipelineResult



logger = logging.getLogger(__name__)





def _norm(text: Optional[str]) -> str:

    return (text or "").strip().lower()





def _match_url_from_raw(raw: dict) -> Optional[str]:

    url = str(raw.get("source_url") or raw.get("url") or "").strip()

    if url:

        return url

    match_id = str(raw.get("match_id") or raw.get("id") or "").strip()

    if match_id:

        return f"https://www.flashscore.com/match/football/x/x/?mid={match_id}"

    return None





def _has_odds(result: LivePipelineResult) -> bool:

    odds_status = (result.sources or {}).get("odds")

    return odds_status in ("ok", "partial", "fixture")





def _is_low_confidence(result: LivePipelineResult) -> bool:

    scored = result.scored_run

    if scored is None:

        return False

    conf = float(scored.prediction.overall_confidence_score or 0.0)

    return conf < LOW_CONFIDENCE_THRESHOLD





def _empty_summary(*, date_from: str, date_to: str, league_keys: Sequence[str]) -> Dict[str, Any]:

    return {

        "pipeline": "league_eval_pool_accumulate",

        "date_from": date_from,

        "date_to": date_to,

        "league_keys": list(league_keys),

        "use_discovery_fallback": False,

        "competitions_processed": [],

        "fixtures_found": 0,

        "fixtures_seen_total": 0,

        "fixtures_in_range": 0,

        "fixtures_in_scope": 0,

        "fixtures_out_of_range_skipped": 0,

        "expected_matches": None,

        "discovery_fixtures_added": 0,

        "discovery_warnings": [],

        "league_full_scored": 0,

        "parked_or_non_league_skipped": 0,

        "out_of_scope_skipped": 0,

        "runs_with_odds": 0,

        "low_confidence_runs": 0,

        "persist_success": 0,

        "persisted_runs": 0,

        "persist_fail": 0,

        "pipeline_fail": 0,

        "runs": [],

        "errors": [],

    }





def _fixture_dedupe_key(raw: dict) -> Tuple[str, str, str]:

    home = str(raw.get("home_team_name") or raw.get("home") or "")

    away = str(raw.get("away_team_name") or raw.get("away") or "")

    mid = str(raw.get("match_id") or raw.get("id") or "")

    return (mid or f"{home}:{away}", home, away)





def accumulate_league_pool(

    *,

    date_from: str,

    date_to: str,

    league_keys: Optional[Sequence[str]] = None,

    db_path: str | Path | None = None,

    scraper_url: Optional[str] = None,

    skip_openclaw: bool = False,

    fetch_matches_for_date: Optional[Callable[[str], List[dict]]] = None,

    pipeline_factory: Optional[Callable[[], LiveFlashscorePipeline]] = None,

    use_discovery_fallback: Optional[bool] = None,

    fetch_fixtures_for_entry_fn: Optional[Callable] = None,

    expected_matches: Optional[int] = None,

) -> Dict[str, Any]:

    """

    Discover fixtures by date from Flashscore, filter wave-1 league scope, run live pipeline

    with persistence for league-eligible matches.



    When ``use_discovery_fallback`` is True (default from ``EVAL_POOL_DISCOVERY_FALLBACK``),

    empty list-by-date for a pool entry triggers ``FixtureDiscoveryService``.

    """

    pool_entries = filter_pool_keys(league_keys)

    keys = tuple(e.key for e in pool_entries)

    fallback = (

        use_discovery_fallback

        if use_discovery_fallback is not None

        else config.EVAL_POOL_DISCOVERY_FALLBACK

    )

    summary = _empty_summary(date_from=date_from, date_to=date_to, league_keys=keys)

    summary["use_discovery_fallback"] = fallback

    summary["expected_matches"] = expected_matches

    competitions_seen: set[str] = set()

    processed_keys: Set[Tuple[str, str, str, str]] = set()



    start = date.fromisoformat(date_from)

    end = date.fromisoformat(date_to)

    if end < start:

        raise ValueError("date_to must be >= date_from")



    if fetch_matches_for_date is None:

        from football_agent.collectors.flashscore.client import FlashscoreCollectorClient



        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")

        if not base:

            raise ValueError("FLASHSCORE_SCRAPER_URL not configured")

        client = FlashscoreCollectorClient(

            base,

            api_key=config.FLASHSCORE_SCRAPER_API_KEY,

            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,

        )

        fetch_matches_for_date = client.fetch_matches_for_date_raw



    entry_fetch = fetch_fixtures_for_entry_fn or fetch_fixtures_for_pool_entry



    def _make_pipeline() -> LiveFlashscorePipeline:

        if pipeline_factory is not None:

            return pipeline_factory()

        return LiveFlashscorePipeline(

            scraper_url=scraper_url,

            skip_openclaw=skip_openclaw,

            db_path=db_path,

            persist=True,

        )



    current = start

    while current <= end:

        date_str = current.isoformat()

        try:

            raw_list = fetch_matches_for_date(date_str)

        except Exception as exc:

            logger.exception("fetch_matches_for_date failed date=%s", date_str)

            summary["errors"].append({"date": date_str, "error": str(exc)})

            current += timedelta(days=1)

            continue



        summary["fixtures_found"] += len(raw_list)



        for entry in pool_entries:

            try:

                fetch_result = entry_fetch(

                    entry,

                    date_str,

                    raw_list,

                    use_discovery_fallback=fallback,

                    wave_date_from=date_from,

                    wave_date_to=date_to,

                )
                if isinstance(fetch_result, FixtureFetchResult):
                    entry_raws = fetch_result.fixtures
                    disc_warnings = fetch_result.warnings
                    fetch_stats = fetch_result.stats
                else:
                    # Legacy test doubles may still return a 3-tuple.
                    entry_raws, disc_warnings, fetch_stats = fetch_result

            except Exception as exc:
                logger.warning("fixture fetch failed entry=%s date=%s: %s", entry.key, date_str, exc)
                summary["discovery_warnings"].append(f"fixture_fetch_error:{entry.key}:{date_str}")
                entry_raws = []
                disc_warnings = []
                fetch_stats = None

            if fetch_stats is not None:
                summary["fixtures_seen_total"] += fetch_stats.seen
                summary["fixtures_out_of_range_skipped"] += fetch_stats.skipped_out_of_range

            for w in disc_warnings:

                if w.startswith("discovery_fallback_used:"):

                    summary["discovery_fixtures_added"] += len(entry_raws)

                summary["discovery_warnings"].append(w)



            for raw in entry_raws:

                explicit_date, fixture_date, in_range = evaluate_fixture_date_guard(
                    raw,
                    date_from,
                    date_to,
                    loop_date=date_str,
                )
                logger.info(
                    "fixture_date_guard entry=%s match_id=%s explicit=%s fixture_date=%s "
                    "in_range=%s discovery=%s loop_day=%s",
                    entry.key,
                    raw.get("match_id"),
                    explicit_date,
                    fixture_date,
                    in_range,
                    is_discovery_fixture(raw),
                    date_str,
                )
                if not in_range or fixture_date is None:
                    logger.error(
                        "OUT_OF_RANGE_FIXTURE blocked entry=%s match_id=%s explicit=%s "
                        "fixture_date=%s wave=%s..%s discovery=%s",
                        entry.key,
                        raw.get("match_id"),
                        explicit_date,
                        fixture_date,
                        date_from,
                        date_to,
                        is_discovery_fixture(raw),
                    )
                    summary["fixtures_out_of_range_skipped"] += 1
                    summary["errors"].append(
                        {
                            "date": date_str,
                            "entry": entry.key,
                            "match_id": raw.get("match_id"),
                            "error": "out_of_range_fixture",
                            "explicit_date": explicit_date,
                            "fixture_date": fixture_date,
                        }
                    )
                    continue

                pool_entry: Optional[LeaguePoolEntry] = entry

                comp_name = str(

                    raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or entry.display_name

                )

                comp_country = raw.get("competition_country") or (entry.countries[0].title() if entry.countries else None)

                if not raw.get("_discovery_source"):

                    resolved = resolve_pool_entry(comp_name, str(comp_country) if comp_country else None)

                    if resolved is None or resolved.key != entry.key:

                        summary["out_of_scope_skipped"] += 1

                        continue



                dedupe = _fixture_dedupe_key(raw)

                proc_key = (fixture_date, entry.key, dedupe[0], dedupe[1])

                if proc_key in processed_keys:

                    continue

                processed_keys.add(proc_key)



                summary["fixtures_in_scope"] += 1

                summary["fixtures_in_range"] += 1

                competitions_seen.add(pool_entry.display_name)



                meta = FlashscoreMeta(

                    match_id=str(raw.get("match_id") or raw.get("id") or "unknown"),

                    source_url=str(raw.get("source_url") or raw.get("url") or ""),

                    competition_name=comp_name,

                    competition_country=str(comp_country) if comp_country else None,

                    home_team_name=str(raw.get("home_team_name") or raw.get("home") or ""),

                    away_team_name=str(raw.get("away_team_name") or raw.get("away") or ""),

                )

                clf = classify_competition_meta(meta)

                if not clf.is_league_eligible:

                    summary["parked_or_non_league_skipped"] += 1

                    continue



                match_url = _match_url_from_raw(raw)

                if not match_url:

                    summary["pipeline_fail"] += 1

                    summary["errors"].append(

                        {

                            "date": date_str,

                            "competition": comp_name,

                            "error": "missing match_url",

                        }

                    )

                    continue



                pipeline = _make_pipeline()

                discovery_hints = None
                if raw.get("_discovery_source"):
                    discovery_hints = {
                        "home_team_name": raw.get("home_team_name") or raw.get("home"),
                        "away_team_name": raw.get("away_team_name") or raw.get("away"),
                        "competition_name": comp_name,
                        "competition_country": comp_country,
                        "fixture_date": fixture_date,
                        "kickoff_utc": raw.get("kickoff_utc"),
                        "match_id": raw.get("match_id"),
                    }

                try:

                    result = pipeline.analyze_flashscore_url(
                        match_url,
                        discovery_hints=discovery_hints,
                    )

                except Exception as exc:

                    summary["pipeline_fail"] += 1

                    summary["errors"].append(

                        {

                            "date": date_str,

                            "match_url": match_url,

                            "competition": comp_name,

                            "error": str(exc),

                        }

                    )

                    logger.exception("pipeline failed url=%s", match_url)

                    continue



                row: Dict[str, Any] = {

                    "date": fixture_date,

                    "pool_key": entry.key,

                    "competition": comp_name,

                    "home": meta.home_team_name,

                    "away": meta.away_team_name,

                    "match_url": match_url,

                    "success": result.success,

                    "persisted": result.persisted,

                    "run_id": result.run_id,

                    "route": result.routing_decision.route if result.routing_decision else None,

                    "discovery": bool(raw.get("_discovery_source")),

                }



                if not result.success:

                    summary["pipeline_fail"] += 1

                    row["error"] = result.user_message or result.stage_failed

                    summary["errors"].append(row)

                    summary["runs"].append(row)

                    continue



                route = result.routing_decision.route if result.routing_decision else None

                if route != "league_full":

                    summary["parked_or_non_league_skipped"] += 1

                    row["skipped"] = "non_league_route"

                    summary["runs"].append(row)

                    continue



                summary["league_full_scored"] += 1

                if _has_odds(result):

                    summary["runs_with_odds"] += 1

                    row["odds"] = True

                if _is_low_confidence(result):

                    summary["low_confidence_runs"] += 1

                    row["low_confidence"] = True



                if result.persisted:

                    summary["persist_success"] += 1

                    summary["persisted_runs"] += 1

                else:

                    summary["persist_fail"] += 1

                    row["persist_failed"] = True



                summary["runs"].append(row)



        current += timedelta(days=1)



    summary["competitions_processed"] = sorted(competitions_seen)

    if expected_matches is not None and summary["fixtures_in_range"] != expected_matches:
        summary["discovery_warnings"].append(
            f"expected_matches_mismatch:expected={expected_matches}:actual_in_range={summary['fixtures_in_range']}"
        )

    if fallback and summary["fixtures_in_scope"] == 0 and not summary["discovery_warnings"]:

        summary["discovery_warnings"].append("list_and_discovery_empty")

    logger.info("league eval pool accumulation done: %s", summary)

    return summary


