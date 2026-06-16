"""
End-to-end live debug trace (HTTP adapters only):

live Flashscore scraper → optional OpenClaw/odds enrichment → merge → snapshot → scorer
→ optional persistence → optional offline evaluation.

Delegates to ``LiveFlashscorePipeline`` (fail-soft enrichment). Not wired into Telegram or app_pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent import config
from football_agent.debug.merged_scoring_trace import build_scoring_summary, _print_summary
from football_agent.services.enrichment_config import resolve_openclaw_base_url
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline, LivePipelineResult
from football_agent.debug.live_service_health import check_live_services
from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2

logger = logging.getLogger(__name__)


def _resolve_skip_openclaw(*, skip: bool, use_openclaw: bool) -> bool:
    if use_openclaw:
        return False
    return skip


def build_live_summary_from_pipeline(
    result: LivePipelineResult,
    *,
    evaluation: Optional[Dict[str, Any]] = None,
    openclaw_requested: bool = False,
) -> Dict[str, Any]:
    if not result.scored_run:
        return {
            "success": result.success,
            "path": result.path,
            "stage_failed": result.stage_failed,
            "user_message": result.user_message,
            "sources": dict(result.sources),
            "source_warnings": list(result.warnings),
        }

    summary = build_scoring_summary(result.scored_run)
    summary["success"] = result.success
    summary["pipeline"] = {
        "success": result.success,
        "path": result.path,
        "persisted": result.persisted,
        "enrichment_mode": result.enrichment_mode,
        "odds_source": result.odds_source,
        "enrichment_backend": result.enrichment_backend,
        "openclaw_requested": openclaw_requested,
    }
    summary["sources"] = dict(result.sources)
    summary["source_warnings"] = list(result.warnings)
    if result.completeness is not None:
        summary["completeness"] = result.completeness.to_debug_dict()
    if result.run_id:
        summary["run_id"] = result.run_id
    if result.match_key:
        summary["match_key"] = result.match_key
    if result.competition_classification is not None:
        summary["competition_classification"] = result.competition_classification.to_debug_dict()
    if result.competition_guardrail is not None:
        summary["competition_guardrail"] = result.competition_guardrail.to_debug_dict()
    if evaluation is not None:
        summary["evaluation"] = evaluation
    return summary


def _print_live_trace(summary: Dict[str, Any]) -> None:
    if not summary.get("success", True) and summary.get("stage_failed"):
        print(f"Pipeline failed: stage={summary.get('stage_failed')}")
        print(summary.get("user_message") or "")
        return

    rep = summary.get("report") or {}
    scoring = summary.get("scoring") or {}
    meta = summary.get("snapshot_meta") or {}

    print("Live analysis trace")
    print(f"- match: {meta.get('competition_name')} | {meta.get('home_team', {}).get('name')} vs {meta.get('away_team', {}).get('name')}")
    print(f"- sources: {summary.get('sources')}")
    print(
        f"- openclaw_link_strategy={rep.get('openclaw_link_strategy')} "
        f"odds_link_strategy={rep.get('odds_link_strategy')}"
    )
    if rep.get("merge_missing_blocks"):
        print(f"- missing_blocks: {rep.get('merge_missing_blocks')}")
    if summary.get("source_warnings"):
        print(f"- source_warnings: {summary.get('source_warnings')[:8]}")
    if summary.get("completeness"):
        comp = summary["completeness"]
        print(f"- coverage_score={comp.get('coverage_score')} flashscore_missing={comp.get('flashscore_missing')}")
    if scoring.get("best_market"):
        bm = scoring["best_market"]
        print(f"- best_market: {bm.get('market_key')} p={bm.get('probability')} book_odds={bm.get('book_odds')}")
    if summary.get("run_id"):
        print(f"- run_id={summary.get('run_id')} match_key={summary.get('match_key')}")
    if summary.get("evaluation"):
        ev = summary["evaluation"]
        counts = ev.get("counts") or {}
        metrics = ev.get("metrics") or {}
        print(
            f"- evaluation: scored={counts.get('scored_runs_total')} "
            f"settled={counts.get('settled_runs_total')} "
            f"coverage={metrics.get('settled_coverage')}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="live_analysis_trace",
        description="Live HTTP ingestion → merge → snapshot → scorer → optional persist/eval (debug only).",
    )
    parser.add_argument("--match-url", help="Flashscore match URL for scraper.")
    parser.add_argument("--flashscore-id", help="Flashscore match id (passed to scraper as match_id).")
    parser.add_argument("--home", help="Home team (with --away and --date).")
    parser.add_argument("--away", help="Away team (with --home and --date).")
    parser.add_argument("--date", help="Match date YYYY-MM-DD (with --home and --away).")
    parser.add_argument("--competition", help="Competition code filter (team/date mode).")
    parser.add_argument("--flashscore-url", help="Override FLASHSCORE_SCRAPER_URL.")
    parser.add_argument("--flashscore-api-key", help="Override FLASHSCORE_SCRAPER_API_KEY.")
    parser.add_argument("--openclaw-url", help="Override OPENCLAW_CONTEXT_BASE_URL / OPENCLAW_BASE_URL.")
    parser.add_argument("--openclaw-api-key", help="Override OPENCLAW_CONTEXT_API_KEY.")
    parser.add_argument(
        "--use-openclaw",
        action="store_true",
        help="Attempt OpenClaw enrichment for this run (fail-soft if URL missing).",
    )
    parser.add_argument("--skip-openclaw", action="store_true", help="Do not call OpenClaw context.")
    parser.add_argument("--odds-fixture", help="Odds fixture stem (requires --fixtures-dir; debug offline odds).")
    parser.add_argument(
        "--fixtures-dir",
        help="Directory for optional odds fixture JSON (e.g. football_agent/tests/data).",
    )
    parser.add_argument("--db-path", help="SQLite path for persistence.")
    parser.add_argument("--no-persist", action="store_true", help="Skip DB write.")
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run OfflineEvaluationServiceV2 after persist (requires DB with match_results for settlement).",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON summary.")
    parser.add_argument(
        "--check-services",
        action="store_true",
        help="Probe Flashscore/OpenClaw /health endpoints and exit (no match run).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.check_services:
        health = [h.to_dict() for h in check_live_services(openclaw_url=args.openclaw_url)]
        payload = {"services": health}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for h in health:
                mark = "OK" if h["ok"] else "FAIL"
                print(f"{h['name']}: {mark} url={h.get('url') or '-'} error={h.get('error') or ''}")
        return 0

    fs_url = (args.flashscore_url or config.FLASHSCORE_SCRAPER_URL or "").strip() or None
    if not fs_url:
        print(
            "ERROR: Flashscore scraper URL required. "
            "Set FLASHSCORE_SCRAPER_URL in .env or pass --flashscore-url.",
        )
        return 2

    match_ref = (args.match_url or args.flashscore_id or "").strip() or None
    home, away, date_str = args.home, args.away, args.date
    if match_ref and (home or away or date_str):
        print("ERROR: Use either --match-url/--flashscore-id or --home/--away/--date, not both.")
        return 2
    if not match_ref and not (home and away and date_str):
        print("ERROR: Provide --match-url, --flashscore-id, or all of --home, --away, --date.")
        return 2

    skip_openclaw = _resolve_skip_openclaw(skip=args.skip_openclaw, use_openclaw=args.use_openclaw)
    openclaw_requested = args.use_openclaw or (not skip_openclaw and bool(resolve_openclaw_base_url(args.openclaw_url)))

    fixtures_dir = Path(args.fixtures_dir) if args.fixtures_dir else None
    if args.odds_fixture and not fixtures_dir:
        print("ERROR: --odds-fixture requires --fixtures-dir.")
        return 2

    pipeline = LiveFlashscorePipeline(
        scraper_url=fs_url,
        scraper_api_key=args.flashscore_api_key,
        openclaw_url=args.openclaw_url,
        openclaw_api_key=args.openclaw_api_key,
        skip_openclaw=skip_openclaw,
        fixtures_dir=fixtures_dir,
        odds_fixture_stem=args.odds_fixture,
        db_path=args.db_path,
        persist=not args.no_persist,
    )

    if match_ref:
        result = pipeline.analyze_flashscore_url(match_ref)
    else:
        result = pipeline.analyze_teams(
            home or "",
            away or "",
            date_str or "",
            competition_code=args.competition,
        )

    if not result.success:
        summary = build_live_summary_from_pipeline(result, openclaw_requested=openclaw_requested)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            _print_live_trace(summary)
            if result.user_message:
                print(result.user_message)
        return 1

    evaluation: Optional[Dict[str, Any]] = None
    if args.evaluate and result.persisted and args.db_path:
        eval_svc = OfflineEvaluationServiceV2(db_path=args.db_path)
        try:
            evaluation = eval_svc.evaluate(limit=50)
        finally:
            eval_svc.close()

    summary = build_live_summary_from_pipeline(
        result,
        evaluation=evaluation,
        openclaw_requested=openclaw_requested,
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_live_trace(summary)
        _print_summary(summary, as_json=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
