"""
Pre-kickoff odds refresh CLI (Refresh A).

Examples:
  USE_COLLECTOR_LAYER=true python -m football_agent.jobs.refresh_odds --match-url "https://..."
  USE_COLLECTOR_LAYER=true python -m football_agent.jobs.refresh_odds --home A --away B --date 2025-06-03
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional, Sequence

from football_agent import config
from football_agent.services.odds_refresh_service import OddsRefreshService

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pre-kickoff Flashscore collector odds refresh")
    p.add_argument("--match-url", help="Flashscore match URL")
    p.add_argument("--home", help="Home team (with --away and --date)")
    p.add_argument("--away", help="Away team")
    p.add_argument("--date", help="Match date YYYY-MM-DD")
    p.add_argument("--competition-code", default=None, help="Optional competition filter")
    p.add_argument("--force", action="store_true", help="Refresh even if odds still fresh")
    p.add_argument("--json", action="store_true", help="Print machine-readable summary")
    p.add_argument("--scraper-url", default=None, help="Override FLASHSCORE_SCRAPER_URL")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)

    if not config.USE_COLLECTOR_LAYER:
        print("USE_COLLECTOR_LAYER=false — enable collector layer for odds refresh", file=sys.stderr)
        return 2

    svc = OddsRefreshService(scraper_url=args.scraper_url)

    if args.match_url:
        result = svc.refresh_for_match_url(args.match_url, force=args.force)
    elif args.home and args.away and args.date:
        result = svc.refresh_for_teams(
            args.home,
            args.away,
            args.date,
            competition_code=args.competition_code,
            force=args.force,
        )
    else:
        print("Provide --match-url or --home --away --date", file=sys.stderr)
        return 2

    summary = result.to_summary_dict()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"success={result.success} refreshed={result.refreshed} skipped={result.skipped}")
        print(f"match_key={result.match_key}")
        print(f"before={summary['before_collected_at_utc']} after={summary['after_collected_at_utc']}")
        if result.store_path:
            print(f"store={result.store_path}")
        if result.warnings:
            print("warnings:")
            for w in result.warnings:
                print(f"  - {w}")
        if result.error_message:
            print(f"error={result.error_message}")

    if not result.success:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
