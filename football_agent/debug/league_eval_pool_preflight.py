"""CLI: league eval-pool preflight before a wave run."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from football_agent.eval_pool.preflight import format_preflight_text, run_preflight


def _parse_expected(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [p.strip() for p in value.split(",") if p.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight check: registry / pool / scraper readiness for league_eval_pool wave.",
    )
    parser.add_argument("--date-from", required=True, help="YYYY-MM-DD inclusive start.")
    parser.add_argument("--date-to", required=True, help="YYYY-MM-DD inclusive end.")
    parser.add_argument(
        "--expected-leagues",
        help="Comma-separated expected league tokens (latvia,ireland,china,...).",
    )
    parser.add_argument("--db-path", help="Reserved for future DB snapshot checks (unused).")
    parser.add_argument("--scraper-url", help="Override FLASHSCORE_SCRAPER_URL.")
    parser.add_argument("--no-fixture-probe", action="store_true", help="Skip live fixture discovery probe.")
    parser.add_argument("--json", action="store_true", help="JSON output.")
    args = parser.parse_args(argv)

    report = run_preflight(
        date_from=args.date_from,
        date_to=args.date_to,
        expected_leagues=_parse_expected(args.expected_leagues),
        probe_fixtures=not args.no_fixture_probe,
        scraper_url=args.scraper_url,
    )

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_preflight_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
