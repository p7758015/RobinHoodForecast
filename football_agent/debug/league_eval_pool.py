"""
CLI for league eval-pool accumulation, settlement, and reporting.

Examples::

  python -m football_agent.debug.league_eval_pool accumulate --date-from 2026-06-01 --date-to 2026-06-03
  python -m football_agent.debug.league_eval_pool settle --date-from 2026-05-28 --date-to 2026-06-03
  python -m football_agent.debug.league_eval_pool report --json
  python -m football_agent.debug.league_eval_pool report --calibration --json
  python -m football_agent.debug.league_eval_pool report --with-calibration
  python -m football_agent.debug.league_eval_pool report --leagues kazakhstan_premier,latvia_virsliga
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Optional, Sequence

from football_agent.eval_pool.accumulate import accumulate_league_pool
from football_agent.eval_pool.report import LeagueEvalPoolReporter
from football_agent.eval_pool.scope import WAVE1_POOL_KEYS
from football_agent.eval_pool.settle import settle_league_pool_from_flashscore

logging.basicConfig(level=logging.INFO)


def _parse_leagues(value: Optional[str]) -> Optional[Sequence[str]]:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _cmd_accumulate(args: argparse.Namespace) -> int:
    use_fallback = not bool(getattr(args, "no_discovery_fallback", False))

    summary = accumulate_league_pool(
        date_from=args.date_from,
        date_to=args.date_to,
        league_keys=_parse_leagues(args.leagues),
        db_path=args.db_path,
        scraper_url=args.scraper_url,
        skip_openclaw=args.skip_openclaw,
        use_discovery_fallback=use_fallback,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("League eval-pool accumulation")
        print(f"- date range: {summary['date_from']} .. {summary['date_to']}")
        print(f"- competitions: {', '.join(summary['competitions_processed']) or '(none)'}")
        print(f"- fixtures found / in scope: {summary['fixtures_found']} / {summary['fixtures_in_scope']}")
        print(f"- discovery fallback: {summary.get('use_discovery_fallback')} added={summary.get('discovery_fixtures_added', 0)}")
        if summary.get("discovery_warnings"):
            print(f"- discovery warnings: {len(summary['discovery_warnings'])}")
        print(f"- league_full scored: {summary['league_full_scored']}")
        print(f"- parked/non-league skipped: {summary['parked_or_non_league_skipped']}")
        print(f"- out of scope skipped: {summary['out_of_scope_skipped']}")
        print(f"- runs with odds: {summary['runs_with_odds']}")
        print(f"- low confidence runs: {summary['low_confidence_runs']}")
        print(f"- persist success/fail: {summary['persist_success']} / {summary['persist_fail']}")
        if summary["errors"]:
            print(f"- errors: {len(summary['errors'])}")
    return 0


def _cmd_settle(args: argparse.Namespace) -> int:
    summary = settle_league_pool_from_flashscore(
        date_from=args.date_from,
        date_to=args.date_to,
        league_keys=_parse_leagues(args.leagues),
        db_path=args.db_path,
        scraper_url=args.scraper_url,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("League eval-pool settlement")
        print(f"- date range: {summary['date_from']} .. {summary['date_to']}")
        print(f"- fixtures scanned / in scope: {summary['fixtures_scanned']} / {summary['fixtures_in_scope']}")
        print(f"- finished in scope: {summary['finished_in_scope']}")
        print(f"- results saved: {summary['results_saved']}")
        print(f"- skipped (not finished / no score): {summary['skipped_not_finished']} / {summary['skipped_no_score']}")
        if summary["errors"]:
            print(f"- errors: {len(summary['errors'])}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    reporter = LeagueEvalPoolReporter(db_path=args.db_path)
    try:
        if getattr(args, "calibration", False):
            report = reporter.build_calibration_review(
                league_keys=_parse_leagues(args.leagues),
                date_from=args.date_from,
                date_to=args.date_to,
                limit=int(args.limit),
            )
        else:
            report = reporter.build_report(
                league_keys=_parse_leagues(args.leagues),
                date_from=args.date_from,
                date_to=args.date_to,
                limit=int(args.limit),
            )
            if getattr(args, "with_calibration", False):
                report["calibration_review"] = reporter.build_calibration_review(
                    league_keys=_parse_leagues(args.leagues),
                    date_from=args.date_from,
                    date_to=args.date_to,
                    limit=int(args.limit),
                )
    finally:
        reporter.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if getattr(args, "calibration", False):
        from football_agent.eval_pool.calibration_report import format_calibration_cli_summary

        print(format_calibration_cli_summary(report))
        return 0

    counts = report.get("counts") or {}
    eval_sub = report.get("evaluation_subset") or {}
    eval_counts = eval_sub.get("counts") or {}
    eval_metrics = eval_sub.get("metrics") or {}

    print("League eval-pool report")
    print(f"- pool: {', '.join(report.get('pool') or [])}")
    print(f"- persisted runs (db total): {counts.get('persisted_runs_total_db')}")
    print(f"- pool runs: {counts.get('pool_runs')}")
    print(f"- league scored (excl. parked): {counts.get('league_scored_runs')}")
    print(f"- parked/analysis_only in pool: {counts.get('parked_or_analysis_only_in_pool')} (share={report.get('parked_share_in_pool')})")
    print(f"- settled league scored: {counts.get('settled_league_scored_runs')}")
    print(f"- odds coverage: {counts.get('runs_with_odds')}")
    print(f"- low confidence: {counts.get('low_confidence_runs')}")
    print(f"- confidence distribution: {report.get('confidence_distribution')}")
    print(f"- best market distribution: {report.get('best_market_distribution')}")
    print(f"- eval settled_runs: {eval_counts.get('settled_runs')} hit_rate={eval_metrics.get('best_market_hit_rate')} roi_mean={eval_metrics.get('roi_mean_profit')}")
    cal = report.get("calibration_review")
    if cal:
        from football_agent.eval_pool.calibration_report import format_calibration_cli_summary

        print("")
        print(format_calibration_cli_summary(cal))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="league_eval_pool",
        description="Accumulate, settle, and report league eval-pool (wave-1 live competitions).",
    )
    parser.add_argument("--db-path", help="SQLite DB path (default football_agent/data/football_agent.db).")
    parser.add_argument("--scraper-url", help="Override FLASHSCORE_SCRAPER_URL.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_acc = sub.add_parser("accumulate", help="Batch score + persist league-eligible fixtures.")
    p_acc.add_argument("--date-from", required=True, help="YYYY-MM-DD inclusive start.")
    p_acc.add_argument("--date-to", required=True, help="YYYY-MM-DD inclusive end.")
    p_acc.add_argument(
        "--leagues",
        help=f"Comma-separated pool keys (default all wave-1: {', '.join(WAVE1_POOL_KEYS)}).",
    )
    p_acc.add_argument("--skip-openclaw", action="store_true", help="Skip OpenClaw enrichment.")
    p_acc.add_argument(
        "--no-discovery-fallback",
        action="store_true",
        help="Disable FixtureDiscoveryService fallback when list-by-date is empty (default: fallback on).",
    )
    p_acc.add_argument("--json", action="store_true")
    p_acc.set_defaults(func=_cmd_accumulate)

    p_set = sub.add_parser("settle", help="Import finished scores into match_results.")
    p_set.add_argument("--date-from", required=True)
    p_set.add_argument("--date-to", required=True)
    p_set.add_argument("--leagues", help="Comma-separated pool keys.")
    p_set.add_argument("--json", action="store_true")
    p_set.set_defaults(func=_cmd_settle)

    p_rep = sub.add_parser("report", help="Pool coverage + evaluation metrics.")
    p_rep.add_argument("--date-from", required=False)
    p_rep.add_argument("--date-to", required=False)
    p_rep.add_argument("--leagues", help="Comma-separated pool keys.")
    p_rep.add_argument("--limit", type=int, default=5000)
    p_rep.add_argument(
        "--calibration",
        action="store_true",
        help="Calibration-style review (confidence/market/league/risk buckets + diagnostics).",
    )
    p_rep.add_argument(
        "--with-calibration",
        action="store_true",
        help="Include calibration_review section in standard report output.",
    )
    p_rep.add_argument("--json", action="store_true")
    p_rep.set_defaults(func=_cmd_report)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
