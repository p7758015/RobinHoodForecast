"""
Arbitrary Flashscore URL live-debug smoke (Flashscore + OpenClaw + Brave).

Examples::

  python -m football_agent.debug.context_smoke --check-services --json
  python -m football_agent.debug.context_smoke --match-url "https://www.flashscore.com/match/..." --json
  python -m football_agent.debug.context_smoke --match-url "..." --write-report football_agent/data/reports/context_smoke.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from football_agent.debug.context_smoke_report import build_context_smoke_report
from football_agent.debug.live_analysis_trace import build_live_summary_from_pipeline
from football_agent.debug.live_service_health import summarize_live_services
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO = "flashscore-openclaw"


def run_context_smoke(
    *,
    match_url: str,
    scenario: str = DEFAULT_SCENARIO,
    skip_openclaw: bool = False,
    openclaw_url: Optional[str] = None,
    as_json: bool = False,
) -> Dict[str, Any]:
    services = summarize_live_services()
    pipeline = LiveFlashscorePipeline(
        skip_openclaw=skip_openclaw,
        openclaw_url=openclaw_url,
    )
    logger.info("Context smoke %s url=%s", scenario, match_url[:80])
    result = pipeline.analyze_flashscore_url(match_url)
    summary = build_live_summary_from_pipeline(
        result,
        openclaw_requested=not skip_openclaw,
    )
    report = build_context_smoke_report(
        match_url=match_url,
        scenario=scenario,
        services=services,
        pipeline_summary=summary,
    )
    exit_code = 0 if result.success else 1
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report.get("human_summary") or "")
        print(f"\nexit_code={exit_code}")
    return {"report": report, "exit_code": exit_code}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="context_smoke",
        description="Live-debug smoke for arbitrary Flashscore match URLs.",
    )
    parser.add_argument(
        "--check-services",
        action="store_true",
        help="Probe Flashscore + OpenClaw (bridge or gateway) and exit.",
    )
    parser.add_argument(
        "--match-url",
        help="Flashscore match URL to analyze.",
    )
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        help=f"Scenario label for reports (default: {DEFAULT_SCENARIO}).",
    )
    parser.add_argument(
        "--skip-openclaw",
        action="store_true",
        help="Flashscore-only path (no OpenClaw enrichment).",
    )
    parser.add_argument(
        "--openclaw-url",
        help="Override OpenClaw/bridge base URL.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output.")
    parser.add_argument(
        "--write-report",
        metavar="PATH",
        help="Write JSON report to file.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.check_services:
        payload = summarize_live_services()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for svc in payload.get("services") or []:
                status = "OK" if svc.get("ok") else "FAIL"
                print(f"{svc.get('name')}: {status} {svc.get('url') or svc.get('error')}")
            print(
                f"effective_openclaw={payload.get('openclaw_effective_backend')} "
                f"all_ok={payload.get('all_ok')}"
            )
        return 0 if payload.get("all_ok") else 1

    if not args.match_url:
        parser.error("--match-url is required unless --check-services is set")
        return 2

    out = run_context_smoke(
        match_url=args.match_url.strip(),
        scenario=args.scenario,
        skip_openclaw=args.skip_openclaw,
        openclaw_url=args.openclaw_url,
        as_json=args.json,
    )
    if args.write_report:
        path = Path(args.write_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(out["report"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not args.json:
            print(f"Report written: {path}")
    return int(out["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
