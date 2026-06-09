"""
OpenClaw context debug trace (fixture-based only).

Loads a raw context fixture, maps it into OpenClawMatchContext, and prints a compact summary.
No HTTP/runtime, no merge, no snapshot/scorer.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.models import OpenClawMatchContext
from football_agent.openclaw_context.service import OpenClawContextIngestionService

logger = logging.getLogger(__name__)


def build_context_summary(ctx: OpenClawMatchContext) -> Dict[str, Any]:
    meta = ctx.meta
    news = ctx.news
    squad = ctx.squad_context
    coach = ctx.coach_context
    mot = ctx.motivation_narrative
    fatigue = ctx.fatigue_schedule_context

    def count_news_items() -> Dict[str, int]:
        if not news:
            return {"home": 0, "away": 0, "match": 0, "total": 0}
        h = len(news.home_news_items)
        a = len(news.away_news_items)
        m = len(news.match_news_items)
        return {"home": h, "away": a, "match": m, "total": h + a + m}

    def squad_counts(side) -> Dict[str, int]:  # noqa: ANN001
        if not side:
            return {"missing": 0, "returning": 0}
        return {
            "missing": len(side.missing_players_context),
            "returning": len(side.returning_players_context),
        }

    return {
        "meta": {
            "match_id": meta.match_id,
            "query_home_team": meta.query_home_team,
            "query_away_team": meta.query_away_team,
            "query_home_team_normalized": meta.query_home_team_normalized,
            "query_away_team_normalized": meta.query_away_team_normalized,
            "query_competition_name": meta.query_competition_name,
            "query_kickoff_utc": meta.query_kickoff_utc.isoformat() if meta.query_kickoff_utc else None,
            "query_date": meta.query_date.isoformat() if meta.query_date else None,
            "query_string": meta.query_string,
            "collected_at_utc": meta.collected_at_utc.isoformat(),
            "context_window_hours": meta.context_window_hours,
        },
        "blocks_present": ctx.provenance.blocks_present,
        "missing_blocks": ctx.provenance.missing_blocks,
        "news": {
            "counts": count_news_items(),
            "source_count": news.source_count if news else None,
            "high_confidence_count": news.high_confidence_count if news else None,
            "conflicting_reports_flag": news.conflicting_reports_flag if news else None,
        },
        "squad_context": {
            "home": squad_counts(squad.home) if squad else {"missing": 0, "returning": 0},
            "away": squad_counts(squad.away) if squad else {"missing": 0, "returning": 0},
            "home_depth_risk_level": squad.home.depth_risk_level if squad else None,
            "away_depth_risk_level": squad.away.depth_risk_level if squad else None,
            "home_rotation_risk_level": squad.home.rotation_risk_level if squad else None,
            "away_rotation_risk_level": squad.away.rotation_risk_level if squad else None,
        },
        "coach_context_present": coach is not None,
        "motivation_narrative_present": mot is not None,
        "fatigue_schedule_context_present": fatigue is not None,
        "extraction_warnings": ctx.provenance.extraction_warnings,
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    meta = summary["meta"]
    print("OpenClaw context trace (fixture)")
    print(f"- {meta.get('query_home_team')} — {meta.get('query_away_team')}")
    if meta.get("query_competition_name") or meta.get("query_date"):
        print(f"- competition={meta.get('query_competition_name')} date={meta.get('query_date')}")
    print(f"- collected_at_utc={meta.get('collected_at_utc')}")
    print("")
    print(f"- blocks_present: {summary.get('blocks_present')}")
    print(f"- missing_blocks: {summary.get('missing_blocks')}")
    print("")
    n = summary.get("news") or {}
    print(f"News: total={((n.get('counts') or {}).get('total'))} sources={n.get('source_count')} high={n.get('high_confidence_count')} conflict={n.get('conflicting_reports_flag')}")
    s = summary.get("squad_context") or {}
    print(f"Squad: home_missing={(s.get('home') or {}).get('missing')} away_missing={(s.get('away') or {}).get('missing')}")
    print(f"Coach context present: {summary.get('coach_context_present')}")
    print(f"Motivation narrative present: {summary.get('motivation_narrative_present')}")
    print(f"Fatigue context present: {summary.get('fatigue_schedule_context_present')}")
    if summary.get("extraction_warnings"):
        print(f"Warnings: {summary.get('extraction_warnings')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="openclaw_context_trace", description="Debug OpenClaw context (fixture-based)")
    parser.add_argument("--fixtures-dir", required=True, help="Directory with OpenClaw context fixtures (.json).")
    parser.add_argument("--fixture", required=True, help="Fixture filename stem (without .json).")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.exists():
        logger.error("Fixtures dir %s does not exist", fixtures_dir)
        return 2

    svc = OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(fixtures_dir))
    ctx = svc.get_context_for_fixture(args.fixture)
    if not ctx:
        print("Context fixture not found.")
        return 1

    summary = build_context_summary(ctx)
    _print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

