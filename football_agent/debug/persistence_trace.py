"""
Trace path (fixtures only):
flashscore + openclaw_context? + odds? -> merge -> builder -> scorer -> persist -> load -> summary.

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
from football_agent.services.persistence_service_v2 import SnapshotPersistenceServiceV2
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.storage.match_key import build_match_key_from_merged

logger = logging.getLogger(__name__)


def build_persistence_summary(run_id: str, match_key: str, loaded) -> Dict[str, Any]:  # noqa: ANN001
    return {
        "run_id": run_id,
        "match_key": match_key,
        "run_status": getattr(loaded, "run_status", None),
        "created_at_utc": getattr(loaded, "created_at_utc", None).isoformat() if getattr(loaded, "created_at_utc", None) else None,
        "has_merged": loaded.merged_context is not None if loaded else False,
        "has_snapshot": loaded.snapshot is not None if loaded else False,
        "has_report": loaded.build_report is not None if loaded else False,
        "has_prediction": loaded.prediction is not None if loaded else False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="persistence_trace", description="Trace v2 run persistence (fixtures).")
    parser.add_argument("--fixtures-dir", required=True, help="Directory with JSON fixtures (tests/data).")
    parser.add_argument("--flashscore-fixture", required=True, help="Flashscore fixture stem (without .json).")
    parser.add_argument("--openclaw-context-fixture", required=False, help="OpenClaw context fixture stem (without .json).")
    parser.add_argument("--odds-fixture", required=False, help="Odds fixture stem (without .json).")
    parser.add_argument("--db-path", required=False, help="SQLite path (default football_agent/data/football_agent.db).")
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

    pers = SnapshotPersistenceServiceV2(db_path=args.db_path)
    run_id = pers.persist_scored_run(merged=merged, scored=scored)
    match_key = build_match_key_from_merged(merged)
    loaded = pers.load_run(run_id)

    summary = build_persistence_summary(run_id, match_key, loaded)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Persistence trace")
        print(f"- run_id={run_id} status={summary.get('run_status')}")
        print(f"- match_key={match_key}")
        print(f"- has: merged={summary.get('has_merged')} snapshot={summary.get('has_snapshot')} report={summary.get('has_report')} prediction={summary.get('has_prediction')}")
    pers.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

