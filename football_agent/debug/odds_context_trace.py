"""
Odds v1 debug trace (fixture-based only).

Loads raw odds fixture -> maps to MatchOddsContext -> prints compact summary.

Note: this is separate from `debug/odds_trace.py` (which traces API-Football odds flow).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.models import MatchOddsContext
from football_agent.odds.service import OddsIngestionService, MARKET_FIELDS

logger = logging.getLogger(__name__)


def build_odds_summary(ctx: MatchOddsContext) -> Dict[str, Any]:
    meta = ctx.meta
    mk = ctx.markets

    quotes: Dict[str, Any] = {}
    for name in MARKET_FIELDS:
        q = getattr(mk, name)
        quotes[name] = (q.odds_value if q is not None else None)

    return {
        "meta": {
            "fixture_id": meta.fixture_id,
            "home_team": meta.home_team,
            "away_team": meta.away_team,
            "competition_name": meta.competition_name,
            "kickoff_utc": meta.kickoff_utc.isoformat() if meta.kickoff_utc else None,
            "odds_format": meta.odds_format,
            "collected_at_utc": meta.collected_at_utc.isoformat(),
            "source_url": meta.source_url,
        },
        "quotes": quotes,
        "missing_markets": list(ctx.provenance.missing_markets),
        "warnings": list(ctx.provenance.extraction_warnings),
        "backend": {
            "name": ctx.provenance.backend_name,
            "version": ctx.provenance.backend_version,
            "adapter_version": ctx.provenance.adapter_version,
        },
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    meta = summary["meta"]
    print("Odds v1 trace (fixture)")
    print(f"- {meta.get('competition_name')}: {meta.get('home_team')} — {meta.get('away_team')}")
    print(f"- kickoff_utc={meta.get('kickoff_utc')} collected_at={meta.get('collected_at_utc')}")
    print(f"- odds_format={meta.get('odds_format')}")
    print("")
    print("Markets:")
    quotes = summary.get("quotes") or {}
    for k in MARKET_FIELDS:
        v = quotes.get(k)
        print(f"- {k}: {v if v is not None else 'n/a'}")
    print("")
    print(f"- missing_markets: {summary.get('missing_markets')}")
    if summary.get("warnings"):
        print(f"- warnings: {summary.get('warnings')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="odds_context_trace", description="Trace normalized odds contract (fixtures).")
    parser.add_argument("--fixtures-dir", required=True, help="Directory with odds fixtures (.json).")
    parser.add_argument("--fixture", required=True, help="Fixture stem (without .json).")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.exists():
        logger.error("Fixtures dir does not exist: %s", fixtures_dir)
        return 2

    svc = OddsIngestionService(FixtureFileOddsAdapter(fixtures_dir))
    ctx = svc.get_odds_for_fixture(args.fixture)
    if not ctx:
        print("Odds fixture not found.")
        return 1

    summary = build_odds_summary(ctx)
    _print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

