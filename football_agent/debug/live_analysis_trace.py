"""
End-to-end live debug trace (HTTP adapters only):

live Flashscore scraper → FlashscoreMatchFacts
optional live OpenClaw context → OpenClawMatchContext
optional fixture odds → MatchOddsContext
→ merge → snapshot → scorer → optional persistence

Not wired into Telegram or app_pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from football_agent import config
from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.debug.merged_scoring_trace import build_scoring_summary, _print_summary
from football_agent.domain.models import Team
from football_agent.flashscore.adapters.errors import (
    FlashscoreScraperConfigurationError,
    FlashscoreScraperError,
    FlashscoreScraperUnavailableError,
)
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.normalizers.team_name_resolver import score_team_query
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.services.odds_live import fetch_odds_for_facts
from football_agent.services.openclaw_context_live import (
    fetch_openclaw_context_for_facts,
    resolve_openclaw_context_url,
)
from football_agent.services.persistence_service_v2 import SnapshotPersistenceServiceV2
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.services.source_completeness import build_completeness_report
from football_agent.storage.match_key import build_match_key_from_merged

logger = logging.getLogger(__name__)


def _resolve_flashscore_url(cli_url: Optional[str]) -> Optional[str]:
    return (cli_url or config.FLASHSCORE_SCRAPER_URL or "").strip() or None


def _pick_facts_by_teams(
    facts_list: List[FlashscoreMatchFacts],
    home_query: str,
    away_query: str,
    *,
    min_score: float = 0.72,
) -> Tuple[Optional[FlashscoreMatchFacts], Optional[str]]:
    if not facts_list:
        return None, "На указанную дату матчей не найдено."

    scored: List[Tuple[FlashscoreMatchFacts, float]] = []
    for facts in facts_list:
        home_team = Team(id=0, name=facts.meta.home_team_name, short_name=facts.meta.home_team_name)
        away_team = Team(id=0, name=facts.meta.away_team_name, short_name=facts.meta.away_team_name)
        sh = score_team_query(home_query, home_team)
        sa = score_team_query(away_query, away_team)
        combined = (sh + sa) / 2.0
        if sh >= 0.5 and sa >= 0.5:
            scored.append((facts, combined))

    if not scored:
        return None, f"Матч не найден: {home_query} — {away_query}."

    scored.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scored[0]
    if best_score < min_score:
        return None, (
            f"Матч не найден уверенно: {home_query} — {away_query} "
            f"(лучший score {best_score:.2f})."
        )
    return best, None


def _fetch_flashscore_facts(
    fs_url: str,
    *,
    match_url: Optional[str],
    home: Optional[str],
    away: Optional[str],
    date_str: Optional[str],
    competition: Optional[str],
    api_key: Optional[str],
) -> Tuple[Optional[FlashscoreMatchFacts], Dict[str, str]]:
    adapter = HttpFlashscoreScraperAdapter(
        fs_url,
        api_key=api_key or config.FLASHSCORE_SCRAPER_API_KEY,
        timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
    )
    service = FlashscoreIngestionService(adapter)

    if match_url:
        facts = service.get_facts_for_match(match_url)
        if not facts:
            return None, {"flashscore": "failed", "flashscore_error": "empty scraper response"}
        return facts, {"flashscore": "ok"}

    if not (home and away and date_str):
        return None, {
            "flashscore": "failed",
            "flashscore_error": "provide --match-url or --home --away --date",
        }

    raw_list = adapter.fetch_matches_for_date(date_str, competition)
    facts_list = [service._map_raw_to_facts(raw) for raw in raw_list]  # type: ignore[attr-defined]
    facts, err = _pick_facts_by_teams(facts_list, home, away)
    if err or not facts:
        return None, {"flashscore": "failed", "flashscore_error": err or "match not found"}
    return facts, {"flashscore": "ok"}


def _fetch_odds_fixture(
    fixtures_dir: Optional[Path],
    odds_fixture: Optional[str],
) -> Tuple[Optional[Any], Dict[str, str]]:
    if not odds_fixture:
        return None, {"odds": "none"}
    if not fixtures_dir:
        return None, {"odds": "failed", "odds_error": "--fixtures-dir required with --odds-fixture"}
    adapter = FixtureFileOddsAdapter(fixtures_dir)
    ctx = OddsIngestionService(adapter).get_odds_for_fixture(odds_fixture)
    if ctx is None:
        return None, {"odds": "failed", "odds_error": f"fixture not found: {odds_fixture}"}
    return ctx, {"odds": "fixture"}


def build_live_summary(
    scored_run,
    *,
    sources: Dict[str, str],
    source_warnings: List[str],
    run_id: Optional[str],
    match_key: Optional[str],
    facts: Optional[FlashscoreMatchFacts] = None,
    openclaw_ctx=None,
    odds_ctx=None,
    openclaw_link: Optional[str] = None,
    odds_link: Optional[str] = None,
) -> Dict[str, Any]:
    summary = build_scoring_summary(scored_run)
    summary["sources"] = dict(sources)
    summary["source_warnings"] = list(source_warnings)
    if facts is not None:
        summary["completeness"] = build_completeness_report(
            facts=facts,
            sources=sources,
            warnings=source_warnings,
            openclaw_ctx=openclaw_ctx,
            odds_ctx=odds_ctx,
            openclaw_link=openclaw_link,
            odds_link=odds_link,
        ).to_debug_dict()
    if run_id:
        summary["run_id"] = run_id
    if match_key:
        summary["match_key"] = match_key
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="live_analysis_trace",
        description="Live HTTP ingestion → merge → snapshot → scorer → optional persist (debug only).",
    )
    parser.add_argument("--match-url", help="Flashscore match URL for scraper.")
    parser.add_argument("--home", help="Home team (with --away and --date).")
    parser.add_argument("--away", help="Away team (with --home and --date).")
    parser.add_argument("--date", help="Match date YYYY-MM-DD (with --home and --away).")
    parser.add_argument("--competition", help="Competition code filter (team/date mode).")
    parser.add_argument("--flashscore-url", help="Override FLASHSCORE_SCRAPER_URL.")
    parser.add_argument("--flashscore-api-key", help="Override FLASHSCORE_SCRAPER_API_KEY.")
    parser.add_argument("--openclaw-url", help="Override OPENCLAW_CONTEXT_BASE_URL.")
    parser.add_argument("--openclaw-api-key", help="Override OPENCLAW_CONTEXT_API_KEY.")
    parser.add_argument("--skip-openclaw", action="store_true", help="Do not call OpenClaw context.")
    parser.add_argument("--odds-fixture", help="Odds fixture stem (requires --fixtures-dir).")
    parser.add_argument(
        "--fixtures-dir",
        help="Directory for optional odds fixture JSON (e.g. football_agent/tests/data).",
    )
    parser.add_argument("--db-path", help="SQLite path for persistence.")
    parser.add_argument("--no-persist", action="store_true", help="Skip DB write.")
    parser.add_argument("--json", action="store_true", help="Output JSON summary.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fs_url = _resolve_flashscore_url(args.flashscore_url)
    if not fs_url:
        print(
            "ERROR: Flashscore scraper URL required. "
            "Set FLASHSCORE_SCRAPER_URL in .env or pass --flashscore-url.",
        )
        return 2

    match_url = args.match_url
    home, away, date_str = args.home, args.away, args.date
    if match_url and (home or away or date_str):
        print("ERROR: Use either --match-url or --home/--away/--date, not both.")
        return 2
    if not match_url and not (home and away and date_str):
        print(
            "ERROR: Provide --match-url or all of --home, --away, --date.",
        )
        return 2

    source_warnings: List[str] = []
    try:
        facts, src = _fetch_flashscore_facts(
            fs_url,
            match_url=match_url,
            home=home,
            away=away,
            date_str=date_str,
            competition=args.competition,
            api_key=args.flashscore_api_key,
        )
    except FlashscoreScraperConfigurationError as e:
        print(f"ERROR: {e}")
        return 2
    except (FlashscoreScraperUnavailableError, FlashscoreScraperError) as e:
        print(f"ERROR: Flashscore scraper unavailable: {e}")
        return 1

    if not facts:
        print(f"ERROR: {src.get('flashscore_error', 'Flashscore ingest failed')}")
        return 1

    sources: Dict[str, str] = dict(src)
    oc_ctx, oc_src, oc_warnings = fetch_openclaw_context_for_facts(
        facts,
        openclaw_url=resolve_openclaw_context_url(args.openclaw_url, skip=args.skip_openclaw),
        api_key=args.openclaw_api_key,
        skip=args.skip_openclaw,
        home_override=home,
        away_override=away,
        date_override=date_str,
        competition_override=args.competition,
    )
    sources.update(oc_src)
    source_warnings.extend(oc_warnings)

    fixtures_dir = Path(args.fixtures_dir) if args.fixtures_dir else None
    if args.odds_fixture:
        odds_ctx, odds_src = _fetch_odds_fixture(fixtures_dir, args.odds_fixture)
        source_warnings_list: List[str] = []
    else:
        odds_ctx, odds_src, source_warnings_list = fetch_odds_for_facts(
            facts,
            home_override=home,
            away_override=away,
            date_override=date_str,
            competition_override=args.competition,
            match_url_override=match_url,
        )
    sources.update(odds_src)
    source_warnings.extend(source_warnings_list)
    if odds_src.get("odds") == "failed" and args.odds_fixture:
        source_warnings.append(f"odds_fixture_failed: {odds_src.get('odds_error')}")

    merged = merge_match_context_v2(facts=facts, openclaw_context=oc_ctx, odds_context=odds_ctx)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)

    run_id: Optional[str] = None
    match_key: Optional[str] = None
    if not args.no_persist:
        pers = SnapshotPersistenceServiceV2(db_path=args.db_path)
        try:
            run_id = pers.persist_scored_run(merged=merged, scored=scored)
            match_key = build_match_key_from_merged(merged)
        finally:
            pers.close()

    summary = build_live_summary(
        scored,
        sources=sources,
        source_warnings=source_warnings,
        run_id=run_id,
        match_key=match_key,
        facts=facts,
        openclaw_ctx=oc_ctx,
        odds_ctx=odds_ctx,
        openclaw_link=report.openclaw_link_strategy,
        odds_link=report.odds_link_strategy,
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Live analysis trace")
        print(f"- sources: {sources}")
        if source_warnings:
            print(f"- source_warnings: {source_warnings}")
        if run_id:
            print(f"- run_id={run_id} match_key={match_key}")
        _print_summary(summary, as_json=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
