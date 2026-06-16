"""
CLI for universal competition + fixture discovery.

Examples::

  python -m football_agent.debug.competition_discovery resolve "лига китая"
  python -m football_agent.debug.competition_discovery fixtures "Chinese Super League" --date-from 2026-06-10
  python -m football_agent.debug.competition_discovery discover "latvia virsliga" --date-from 2026-06-10 --json
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Optional, Sequence

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService

logging.basicConfig(level=logging.INFO)


def _cmd_resolve(args: argparse.Namespace) -> int:
    svc = CompetitionResolverService(scraper_url=args.scraper_url)
    result = svc.resolve_competition(
        args.query,
        limit=int(args.limit),
        allow_ambiguous=bool(args.allow_ambiguous),
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Query: {result.query}")
        print(f"Resolved: {result.resolved.candidate.competition_name if result.resolved else None}")
        print(f"Ambiguous: {result.ambiguous}")
        print(f"Candidates: {len(result.candidates)}")
        for c in result.candidates[:5]:
            print(f"  - {c.competition_name} ({c.country}) [{c.source}] {c.url}")
        if result.warnings:
            print(f"Warnings: {', '.join(result.warnings)}")
    return 0


def _cmd_fixtures(args: argparse.Namespace) -> int:
    resolver = CompetitionResolverService(scraper_url=args.scraper_url)
    resolve = resolver.resolve_competition(
        args.query,
        allow_ambiguous=bool(args.allow_ambiguous),
    )
    if resolve.resolved is None:
        payload = {"resolve": resolve.to_dict(), "fixtures": None}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("Competition not resolved.")
            print(f"Warnings: {', '.join(resolve.warnings)}")
        return 1

    fixture_svc = FixtureDiscoveryService(
        scraper_url=args.scraper_url,
        resolver=resolver,
    )
    fixtures = fixture_svc.list_competition_fixtures(
        resolve.resolved,
        date_from=args.date_from,
        date_to=args.date_to or args.date_from,
    )
    payload = fixtures.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        comp = resolve.resolved.candidate
        print(f"Competition: {comp.competition_name} ({comp.country})")
        print(f"Date range: {fixtures.date_from} .. {fixtures.date_to}")
        print(f"Fixtures: {fixtures.count}")
        for f in fixtures.fixtures[:10]:
            print(f"  - {f.match_date} {f.home_team} vs {f.away_team}")
        if fixtures.warnings:
            print(f"Warnings: {', '.join(fixtures.warnings)}")
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    svc = FixtureDiscoveryService(scraper_url=args.scraper_url)
    result = svc.resolve_and_list_fixtures(
        args.query,
        date_from=args.date_from,
        date_to=args.date_to or args.date_from,
        allow_ambiguous=bool(args.allow_ambiguous),
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        c = result.competition.candidate
        print(f"Query resolved to: {c.competition_name} ({c.country}) source={c.source}")
        print(f"Fixtures: {result.count}")
        if result.warnings:
            print(f"Warnings: {', '.join(result.warnings)}")
    return 0 if result.count or "no_fixtures" in " ".join(result.warnings) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Universal competition discovery (Flashscore-first).")
    parser.add_argument("--scraper-url", help="Override FLASHSCORE_SCRAPER_URL")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_res = sub.add_parser("resolve", help="Resolve competition from text query")
    p_res.add_argument("query")
    p_res.add_argument("--limit", type=int, default=8)
    p_res.add_argument("--allow-ambiguous", action="store_true")
    p_res.set_defaults(func=_cmd_resolve)

    p_fix = sub.add_parser("fixtures", help="Resolve + list fixtures for date range")
    p_fix.add_argument("query")
    p_fix.add_argument("--date-from", required=True)
    p_fix.add_argument("--date-to", required=False)
    p_fix.add_argument("--allow-ambiguous", action="store_true")
    p_fix.set_defaults(func=_cmd_fixtures)

    p_all = sub.add_parser("discover", help="One-shot resolve + fixtures")
    p_all.add_argument("query")
    p_all.add_argument("--date-from", required=True)
    p_all.add_argument("--date-to", required=False)
    p_all.add_argument("--allow-ambiguous", action="store_true")
    p_all.set_defaults(func=_cmd_discover)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
