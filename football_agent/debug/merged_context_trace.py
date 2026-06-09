"""
Merged context trace (fixture-based): Flashscore facts + derived + OpenClaw context.

No scorer, no MatchAnalysisSnapshotV2, no pipeline wiring.
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
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService

logger = logging.getLogger(__name__)


def build_merged_summary(merged) -> Dict[str, Any]:  # noqa: ANN001
    h = merged.headline
    return {
        "headline": h.model_dump(mode="json"),
        "link_strategy": merged.provenance.match_link_strategy,
        "odds_link_strategy": merged.provenance.odds_link_strategy,
        "warnings": merged.provenance.warnings,
        "blocks_present": merged.provenance.blocks_present,
        "missing_blocks": merged.provenance.missing_blocks,
        "flashscore_blocks_missing": merged.flashscore_facts.provenance.missing_blocks,
        "openclaw_blocks_missing": merged.openclaw_context.provenance.missing_blocks if merged.openclaw_context else None,
        "odds_missing_markets": merged.odds_context.provenance.missing_markets if merged.odds_context else None,
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    h = summary["headline"]
    print("Merged pre-snapshot context")
    print(f"- {h.get('competition_name')}: {h.get('home_team')} — {h.get('away_team')}")
    print(f"- kickoff_utc={h.get('kickoff_utc')}")
    print(
        f"- season_phase={h.get('season_phase')} gaps(points): "
        f"title={h.get('gap_to_title_points')} europe={h.get('gap_to_europe_points')} safety={h.get('gap_to_relegation_safety_points')}"
    )
    print(f"- openclaw_context_present={h.get('openclaw_context_present')}")
    print(f"- odds_present={h.get('odds_present')} odds_missing_count={h.get('odds_missing_count')}")
    if h.get("odds_present"):
        print("Odds headline:")
        for k in (
            "home_win_odds",
            "away_win_odds",
            "double_chance_1x_odds",
            "double_chance_x2_odds",
            "btts_yes_odds",
            "home_team_to_score_yes_odds",
            "away_team_to_score_yes_odds",
            "over_1_5_odds",
            "under_3_5_odds",
        ):
            print(f"- {k}: {h.get(k)}")
    print("")
    print(f"- link_strategy: {summary.get('link_strategy')}")
    print(f"- odds_link_strategy: {summary.get('odds_link_strategy')}")
    if summary.get("warnings"):
        print(f"- warnings: {summary.get('warnings')}")
    print(f"- blocks_present: {summary.get('blocks_present')}")
    print(f"- missing_blocks: {summary.get('missing_blocks')}")
    if summary.get("odds_missing_markets") is not None:
        print(f"- odds_missing_markets: {summary.get('odds_missing_markets')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="merged_context_trace", description="Trace merged pre-snapshot context (fixtures).")
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
    summary = build_merged_summary(merged)
    _print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

