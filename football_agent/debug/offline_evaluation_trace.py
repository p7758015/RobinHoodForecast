"""
CLI: offline evaluation over persisted analysis runs.

Reads only persisted artifacts + match_results (no future leakage).
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="offline_evaluation_trace", description="Offline evaluation over persisted v2 runs.")
    parser.add_argument("--db-path", required=False, help="SQLite path (default football_agent/data/football_agent.db).")
    parser.add_argument("--date-from", required=False, help="Kickoff UTC ISO lower bound (or YYYY-MM-DD).")
    parser.add_argument("--date-to", required=False, help="Kickoff UTC ISO upper bound (or YYYY-MM-DD).")
    parser.add_argument("--match-key", required=False, help="Evaluate a single match_key.")
    parser.add_argument("--competition-code", required=False, help="Filter by competition_code.")
    parser.add_argument("--limit", type=int, default=1000, help="Max scored runs to scan.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    svc = OfflineEvaluationServiceV2(db_path=args.db_path)
    report = svc.evaluate(
        date_from=args.date_from,
        date_to=args.date_to,
        match_key=args.match_key,
        competition_code=args.competition_code,
        limit=int(args.limit),
    )
    svc.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    counts = report.get("counts") or {}
    metrics = report.get("metrics") or {}
    print("Offline evaluation (v2)")
    print(f"- scored_runs: {counts.get('scored_runs')}")
    print(f"- settled_runs: {counts.get('settled_runs')} (coverage={metrics.get('settled_coverage')})")
    print(f"- best_market_hit_rate: {metrics.get('best_market_hit_rate')}")
    print(f"- roi_subset: {counts.get('roi_subset')} roi_mean_profit: {metrics.get('roi_mean_profit')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

