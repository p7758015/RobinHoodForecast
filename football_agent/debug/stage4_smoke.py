"""
Stage 4 operational smoke runner — Brazil Serie B league matches (debug/CLI only).

Runs ``live_analysis_trace`` scenarios against canonical Flashscore URLs.
Does not touch Telegram, app_pipeline, or scorer weights.

Examples::

  python -m football_agent.debug.stage4_smoke --check-services
  python -m football_agent.debug.stage4_smoke --scenario flashscore-only --match avai --json
  python -m football_agent.debug.stage4_smoke --scenario flashscore-openclaw --match all --json
  python -m football_agent.debug.stage4_smoke --scenario persist-eval --match avai --db-path football_agent/data/live_stage4.db
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent.debug.live_analysis_trace import build_live_summary_from_pipeline
from football_agent.debug.live_service_health import check_live_services
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline
from football_agent.services.offline_evaluation_service_v2 import OfflineEvaluationServiceV2

logger = logging.getLogger(__name__)

# Canonical Brazil Serie B smoke matches (league-only; not WC/cups).
STAGE4_SMOKE_MATCHES: Dict[str, Dict[str, str]] = {
    "avai": {
        "label": "Avai vs Ceara",
        "match_url": "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc",
        "flashscore_id": "6FiXiHcc",
    },
    "goias": {
        "label": "Goias vs Novorizontino",
        "match_url": "https://www.flashscore.com/match/football/goias-hfAZyE0t/novorizontino-4lOgZPQl/?mid=vs5vjeS9",
        "flashscore_id": "vs5vjeS9",
    },
    "athletic": {
        "label": "Athletic Club vs Sport Recife",
        "match_url": "https://www.flashscore.com/match/football/athletic-club-INXlw5Bp/sport-recife-KIBeGAFO/?mid=zDA7ydDG",
        "flashscore_id": "zDA7ydDG",
    },
}


@dataclass(frozen=True)
class SmokeScenario:
    name: str
    skip_openclaw: bool
    use_openclaw: bool
    openclaw_url: Optional[str]
    persist: bool
    evaluate: bool


SCENARIOS: Dict[str, SmokeScenario] = {
    "flashscore-only": SmokeScenario(
        name="flashscore-only",
        skip_openclaw=True,
        use_openclaw=False,
        openclaw_url=None,
        persist=False,
        evaluate=False,
    ),
    "flashscore-openclaw": SmokeScenario(
        name="flashscore-openclaw",
        skip_openclaw=False,
        use_openclaw=True,
        openclaw_url=None,
        persist=False,
        evaluate=False,
    ),
    "openclaw-degraded": SmokeScenario(
        name="openclaw-degraded",
        skip_openclaw=False,
        use_openclaw=True,
        openclaw_url="http://127.0.0.1:9",  # intentionally unreachable
        persist=False,
        evaluate=False,
    ),
    "persist-eval": SmokeScenario(
        name="persist-eval",
        skip_openclaw=True,
        use_openclaw=False,
        openclaw_url=None,
        persist=True,
        evaluate=True,
    ),
}


def _resolve_matches(match_key: str) -> List[tuple[str, Dict[str, str]]]:
    key = match_key.strip().lower()
    if key == "all":
        return list(STAGE4_SMOKE_MATCHES.items())
    if key not in STAGE4_SMOKE_MATCHES:
        raise SystemExit(f"Unknown --match {match_key!r}. Choose: {', '.join(STAGE4_SMOKE_MATCHES)} or all")
    return [(key, STAGE4_SMOKE_MATCHES[key])]


def run_smoke(
    *,
    scenario_name: str,
    match_key: str = "avai",
    db_path: Optional[str] = None,
    as_json: bool = False,
) -> Dict[str, Any]:
    if scenario_name not in SCENARIOS:
        raise SystemExit(f"Unknown scenario {scenario_name!r}. Choose: {', '.join(SCENARIOS)}")
    scenario = SCENARIOS[scenario_name]
    results: List[Dict[str, Any]] = []
    exit_code = 0

    skip_oc = scenario.skip_openclaw and not scenario.use_openclaw

    for key, meta in _resolve_matches(match_key):
        logger.info("Stage4 smoke %s / %s (%s)", scenario_name, key, meta["label"])
        pipeline = LiveFlashscorePipeline(
            skip_openclaw=skip_oc,
            openclaw_url=scenario.openclaw_url,
            db_path=db_path if scenario.persist else None,
            persist=scenario.persist,
        )
        result = pipeline.analyze_flashscore_url(meta["match_url"])
        evaluation = None
        if scenario.evaluate and result.persisted and db_path:
            eval_svc = OfflineEvaluationServiceV2(db_path=db_path)
            try:
                evaluation = eval_svc.evaluate(limit=50)
            finally:
                eval_svc.close()
        summary = build_live_summary_from_pipeline(
            result,
            evaluation=evaluation,
            openclaw_requested=scenario.use_openclaw,
        )
        summary["smoke"] = {
            "scenario": scenario_name,
            "match_key": key,
            "label": meta["label"],
            "match_url": meta["match_url"],
            "flashscore_id": meta["flashscore_id"],
        }
        if not result.success:
            exit_code = max(exit_code, 1)
        results.append(summary)

    payload = {
        "scenario": scenario_name,
        "matches": results,
        "all_success": all(r.get("success") for r in results),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            sm = r.get("smoke") or {}
            print(f"[{sm['match_key']}] {sm.get('label')} success={r.get('success')}")
            print(f"  sources={r.get('sources')}")
            rep = r.get("report") or {}
            print(f"  missing_blocks={rep.get('merge_missing_blocks')}")
            print(f"  links openclaw={rep.get('openclaw_link_strategy')} odds={rep.get('odds_link_strategy')}")
            scoring = r.get("scoring") or {}
            bm = scoring.get("best_market") or {}
            print(f"  best_market={bm.get('market_key')} p={bm.get('probability')}")
    return {"payload": payload, "exit_code": exit_code}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stage4_smoke",
        description="Stage 4 Brazil Serie B live-debug smoke runner (Flashscore + optional OpenClaw).",
    )
    parser.add_argument(
        "--check-services",
        action="store_true",
        help="Probe FLASHSCORE_SCRAPER_URL/health and OPENCLAW_*/health then exit.",
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS.keys()),
        default="flashscore-only",
        help="Smoke scenario (default: flashscore-only).",
    )
    parser.add_argument(
        "--match",
        default="avai",
        help="Match key: avai | goias | athletic | all (default: avai).",
    )
    parser.add_argument(
        "--db-path",
        default="football_agent/data/live_stage4.db",
        help="SQLite path for persist-eval scenario.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output.")
    parser.add_argument(
        "--write-report",
        metavar="PATH",
        help="Also write JSON report to file (e.g. football_agent/data/reports/stage4_smoke.json).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.check_services:
        health = [h.to_dict() for h in check_live_services()]
        payload = {"services": health, "all_ok": all(h["ok"] for h in health)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for h in health:
                status = "OK" if h["ok"] else "FAIL"
                print(f"{h['name']}: {status} {h.get('url') or h.get('error')}")
        return 0 if payload["all_ok"] else 1

    out = run_smoke(
        scenario_name=args.scenario,
        match_key=args.match,
        db_path=args.db_path,
        as_json=args.json,
    )
    if args.write_report:
        path = Path(args.write_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(out["payload"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not args.json:
            print(f"Report written: {path}")
    return int(out["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
