"""
CLI: offline v2 calibration batch + reports.

Examples:
  python -m football_agent.offline.v2_calibrate --mode run --start 2024-08-01 --end 2024-08-07 --competition PL
  python -m football_agent.offline.v2_calibrate --mode report
  python -m football_agent.offline.v2_calibrate --mode report --export football_agent/data/reports/v2_calibration.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from football_agent.offline.v2_calibration_runner import run_v2_for_date, run_v2_for_date_range
from football_agent.offline.v2_reports import build_full_v2_report
from football_agent.paths import DATA_DIR, ensure_runtime_dirs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _print_summary(report: dict) -> None:
    acc = report.get("accuracy", {}).get("overall", {})
    print(f"Stored v2 rows: {report.get('stored_predictions', 0)}")
    print(f"Settled sample: {report.get('accuracy', {}).get('settled_rows', 0)}")
    print(
        f"Overall winrate: {acc.get('winrate', 0):.1%} "
        f"({acc.get('wins', 0)}/{acc.get('total', 0)})"
    )
    print("\nBy market:")
    for market, stats in (report.get("by_market") or {}).items():
        print(
            f"  {market}: winrate={stats.get('winrate', 0):.1%} "
            f"n={stats.get('total', 0)} avg_p={stats.get('avg_probability', 0):.3f} "
            f"gap={stats.get('calibration_gap', 0):+.3f}"
        )
    print("\nProbability buckets:")
    for b in report.get("calibration", {}).get("probability_buckets", []):
        print(
            f"  {b['bucket']}: n={b['count']} pred={b['predicted_avg']:.3f} "
            f"actual={b['actual_winrate']:.3f} gap={b['gap']:+.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="v2 offline calibration")
    parser.add_argument("--mode", choices=("run", "report", "all"), default="report")
    parser.add_argument("--date", help="Single date YYYY-MM-DD (run mode)")
    parser.add_argument("--start", help="Range start YYYY-MM-DD")
    parser.add_argument("--end", help="Range end YYYY-MM-DD")
    parser.add_argument("--competition", help="League code e.g. PL")
    parser.add_argument(
        "--export",
        help="Write JSON report to path (default: data/reports/v2_calibration.json for report mode)",
    )
    args = parser.parse_args()

    if args.mode in ("run", "all"):
        if args.date:
            summary = run_v2_for_date(args.date, args.competition)
            print(json.dumps(summary, indent=2))
        elif args.start and args.end:
            summaries = run_v2_for_date_range(args.start, args.end, args.competition)
            print(json.dumps(summaries, indent=2))
        else:
            raise SystemExit("run mode requires --date or --start and --end")

    if args.mode in ("report", "all"):
        report = build_full_v2_report()
        _print_summary(report)
        if args.export or args.mode == "report":
            ensure_runtime_dirs()
            out = Path(args.export) if args.export else DATA_DIR / "reports" / "v2_calibration.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nReport saved: {out}")


if __name__ == "__main__":
    main()
