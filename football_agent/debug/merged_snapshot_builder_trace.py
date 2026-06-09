"""
Trace path: fixtures → (flashscore + openclaw_context + odds) → merge → builder → snapshot (+ report).

No scorer, no MatchAnalysisSnapshotV2 scoring, no pipeline wiring.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService

logger = logging.getLogger(__name__)


def build_snapshot_summary(snapshot, report) -> Dict[str, Any]:  # noqa: ANN001
    meta = snapshot.match_meta
    return {
        "match_meta": meta.model_dump(mode="json"),
        "source_tags": list(snapshot.source_tags),
        "odds_present": snapshot.odds is not None and any(
            getattr(snapshot.odds, k) is not None
            for k in (
                "home_win",
                "away_win",
                "home_not_lose",
                "away_not_lose",
                "btts_yes",
                "home_team_to_score",
                "away_team_to_score",
                "over_15",
            )
        ),
        "news_items_count": len(snapshot.news_context.major_news_items) if snapshot.news_context else 0,
        "report": {
            "merge_warnings": report.merge_warnings,
            "merge_missing_blocks": report.merge_missing_blocks,
            "openclaw_link_strategy": report.openclaw_link_strategy,
            "odds_link_strategy": report.odds_link_strategy,
            "builder_warnings": report.builder_warnings,
            "id_generation_notes": report.id_generation_notes,
        },
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    meta = summary["match_meta"]
    rep = summary["report"]
    print("Merged → Snapshot builder trace")
    print(f"- {meta.get('competition_name')}: {meta.get('home_team', {}).get('name')} — {meta.get('away_team', {}).get('name')}")
    print(f"- kickoff_utc={meta.get('match_date_utc')} season_phase={meta.get('season_phase')}")
    print(f"- odds_present={summary.get('odds_present')} news_items_count={summary.get('news_items_count')}")
    print(f"- openclaw_link_strategy={rep.get('openclaw_link_strategy')} odds_link_strategy={rep.get('odds_link_strategy')}")
    if rep.get("merge_missing_blocks"):
        print(f"- missing_blocks: {rep.get('merge_missing_blocks')}")
    if rep.get("merge_warnings") or rep.get("builder_warnings"):
        print(f"- warnings: merge={rep.get('merge_warnings')} builder={rep.get('builder_warnings')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="merged_snapshot_builder_trace",
        description="Trace building MatchAnalysisSnapshotV2 from merged context (fixtures).",
    )
    parser.add_argument("--fixtures-dir", required=True, help="Directory with JSON fixtures (tests/data).")
    parser.add_argument("--flashscore-fixture", required=True, help="Flashscore fixture stem (without .json).")
    parser.add_argument("--openclaw-context-fixture", required=False, help="OpenClaw context fixture stem (without .json).")
    parser.add_argument("--odds-fixture", required=False, help="Odds fixture stem (without .json).")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.exists():
        logger.error("Fixtures dir does not exist: %s", fixtures_dir)
        return 2

    fs = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(fixtures_dir))
    facts = fs.get_facts_for_match(args.flashscore_fixture)
    if not facts:
        print("Flashscore fixture not found.")
        return 1

    oc_ctx = None
    if args.openclaw_context_fixture:
        oc = OpenClawContextIngestionService(FixtureFileOpenClawContextAdapter(fixtures_dir))
        oc_ctx = oc.get_context_for_fixture(args.openclaw_context_fixture)

    odds_ctx = None
    if args.odds_fixture:
        odds = OddsIngestionService(FixtureFileOddsAdapter(fixtures_dir))
        odds_ctx = odds.get_odds_for_fixture(args.odds_fixture)

    merged = merge_match_context_v2(facts=facts, openclaw_context=oc_ctx, odds_context=odds_ctx)
    builder = MergedSnapshotBuilderV2()
    snapshot, report = builder.build_with_report(merged)

    summary = build_snapshot_summary(snapshot, report)
    _print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

