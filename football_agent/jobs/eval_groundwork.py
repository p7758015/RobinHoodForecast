"""CLI: Phase Evaluation A coverage metrics from odds refresh store."""

from __future__ import annotations

import argparse
import json
import sys

from football_agent.services.evaluation_groundwork_service import EvaluationGroundworkService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation A — odds coverage groundwork")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args(argv)

    report = EvaluationGroundworkService().summarize_refresh_store()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        cov = report.get("odds_coverage", {})
        print(f"sample_size={report.get('sample_size', 0)}")
        print(f"any_odds_rate={cov.get('any_odds_rate')}")
        print(f"parlay_usable_rate={cov.get('parlay_usable_rate')}")
        print(f"by_group={cov.get('by_group')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
