"""
Trace path (fixtures only):
flashscore + openclaw_context? + odds? -> merge -> builder -> snapshot -> scorer -> scoring summary.

No Telegram, no app_pipeline wiring, no runtime OpenClaw, no HTTP calls.
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
from football_agent.debug.scorer_inspection import build_scorer_inspection
from football_agent.services.scoring_service_v2 import ScoringServiceV2

logger = logging.getLogger(__name__)


def build_scoring_summary(scored_run) -> Dict[str, Any]:  # noqa: ANN001
    snap = scored_run.snapshot
    pred = scored_run.prediction
    rep = scored_run.build_report

    best = pred.best_market.model_dump(mode="json") if pred.best_market else None
    scoring_block: Dict[str, Any] = {
        "best_market": best,
        "markets_count": len(pred.market_predictions),
        "overall_confidence_score": pred.overall_confidence_score,
        "express_safety": pred.express_safety.model_dump(mode="json"),
        "scoring_warnings": list(scored_run.scoring_warnings),
        "scorer_name": scored_run.scorer_name,
        "scoring_skipped": scored_run.scoring_skipped,
        "analysis_mode": pred.analysis_mode,
        "prediction_mode": pred.prediction_mode,
        "prediction_summary": pred.prediction_summary,
    }
    if scored_run.routing_decision is not None:
        rd = scored_run.routing_decision
        scoring_block["routing_decision"] = {
            "route": rd.route,
            "reason": rd.reason,
            "tournament_type": rd.tournament_type.value,
            "category": rd.category.value,
        }
    if pred.parked_context is not None:
        scoring_block["parked_context"] = pred.parked_context.model_dump(mode="json")
    if not scored_run.scoring_skipped:
        scoring_block["factor_inspection"] = build_scorer_inspection(scored_run)

    return {
        "snapshot_meta": snap.match_meta.model_dump(mode="json"),
        "source_tags": list(snap.source_tags),
        "report": {
            "merge_warnings": rep.merge_warnings,
            "merge_missing_blocks": rep.merge_missing_blocks,
            "openclaw_link_strategy": rep.openclaw_link_strategy,
            "odds_link_strategy": rep.odds_link_strategy,
            "builder_warnings": rep.builder_warnings,
            "id_generation_notes": rep.id_generation_notes,
        },
        "scoring": scoring_block,
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    meta = summary["snapshot_meta"]
    rep = summary["report"]
    scoring = summary["scoring"]

    print("Merged → Snapshot → Scorer trace")
    print(f"- {meta.get('competition_name')}: {meta.get('home_team', {}).get('name')} — {meta.get('away_team', {}).get('name')}")
    print(f"- kickoff_utc={meta.get('match_date_utc')} season_phase={meta.get('season_phase')}")
    print(f"- openclaw_link_strategy={rep.get('openclaw_link_strategy')} odds_link_strategy={rep.get('odds_link_strategy')}")
    if rep.get("merge_missing_blocks"):
        print(f"- missing_blocks: {rep.get('merge_missing_blocks')}")
    if rep.get("merge_warnings") or rep.get("builder_warnings"):
        print(f"- warnings: merge={rep.get('merge_warnings')} builder={rep.get('builder_warnings')}")
    if scoring.get("best_market"):
        bm = scoring["best_market"]
        print(f"- best_market: {bm.get('market_key')} p={bm.get('probability')} book_odds={bm.get('book_odds')}")
    elif scoring.get("analysis_mode") == "analysis_only":
        print(f"- analysis_mode: analysis_only reason={scoring.get('routing_decision', {}).get('reason')}")
        if scoring.get("prediction_summary"):
            print(f"- summary: {scoring.get('prediction_summary')[:120]}...")
    if scoring.get("scoring_warnings"):
        print(f"- scoring_warnings: {scoring.get('scoring_warnings')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="merged_scoring_trace",
        description="Trace scoring on snapshots built from merged context (fixtures).",
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
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)

    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report)
    summary = build_scoring_summary(scored)
    _print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

