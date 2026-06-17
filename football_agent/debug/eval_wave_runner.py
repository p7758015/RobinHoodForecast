"""
Operational eval wave runner — one entrypoint for accumulate / results / settle / report.

Examples::

  cd football_agent
  python -m football_agent.debug.eval_wave_runner full-wave --preset june18_21_first_batch
  python -m football_agent.debug.eval_wave_runner accumulate-wave --preset june18_21_first_batch
  python -m football_agent.debug.eval_wave_runner report-wave --preset june18_21_first_batch --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional, Sequence

from football_agent.eval_pool.wave_manifest import BUILTIN_PRESETS, load_wave_manifest
from football_agent.eval_pool.wave_runner import EvalWaveRunner
from football_agent.paths import DEFAULT_DB_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _add_wave_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=sorted(BUILTIN_PRESETS.keys()),
        help="Built-in wave manifest preset.",
    )
    parser.add_argument("--manifest", help="Path to custom wave manifest JSON.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
    parser.add_argument("--scraper-url", help="Override FLASHSCORE_SCRAPER_URL.")
    parser.add_argument("--skip-openclaw", action="store_true")
    parser.add_argument(
        "--no-discovery-fallback",
        action="store_true",
        help="Disable discovery fallback during accumulation.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def _runner_from_args(args: argparse.Namespace) -> EvalWaveRunner:
    manifest = load_wave_manifest(preset=args.preset, manifest_path=args.manifest)
    return EvalWaveRunner(
        manifest=manifest,
        db_path=args.db_path,
        scraper_url=args.scraper_url,
        skip_openclaw=bool(args.skip_openclaw),
        use_discovery_fallback=False if args.no_discovery_fallback else None,
    )


def _cmd_accumulate(args: argparse.Namespace) -> int:
    runner = _runner_from_args(args)
    result = runner.accumulate_wave()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Accumulate wave done")
        print(f"- fixtures in scope: {result.get('fixtures_in_scope')}")
        print(f"- league scored: {result.get('league_full_scored')}")
        print(f"- persisted: {result.get('persist_success')}")
        print(f"- discovery added: {result.get('discovery_fixtures_added', 0)}")
        if result.get("errors"):
            print(f"- errors: {len(result['errors'])}")
    return 0


def _cmd_update_results(args: argparse.Namespace) -> int:
    runner = _runner_from_args(args)
    result = runner.update_results()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Update results done")
        print(f"- results saved: {result.get('results_saved')}")
        print(f"- finished in scope: {result.get('finished_in_scope')}")
        print(f"- not finished: {result.get('skipped_not_finished')}")
    return 0


def _cmd_settle(args: argparse.Namespace) -> int:
    runner = _runner_from_args(args)
    result = runner.settle_wave()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Settlement stats")
        print(f"- settled evaluable: {result.get('settled_evaluable')}")
        print(f"- unresolved: {result.get('unsettled')}")
        print(f"- hit rate: {result.get('hit_rate')}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    runner = _runner_from_args(args)
    result = runner.report_wave(write_artifacts=not args.no_artifacts)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("cli_summary") or "Report complete")
    return 0


def _cmd_full(args: argparse.Namespace) -> int:
    runner = _runner_from_args(args)
    result = runner.full_wave(write_artifacts=not args.no_artifacts)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("cli_summary") or "Full wave complete")
        if result.get("output_paths"):
            print("")
            for k, v in result["output_paths"].items():
                print(f"{k}: {v}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval_wave_runner",
        description="Operational eval wave: accumulate → results → settle → calibration report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("accumulate-wave", _cmd_accumulate),
        ("update-results", _cmd_update_results),
        ("settle-wave", _cmd_settle),
        ("report-wave", _cmd_report),
        ("full-wave", _cmd_full),
    ):
        p = sub.add_parser(name, help=handler.__doc__ or name)
        _add_wave_args(p)
        if name in ("report-wave", "full-wave"):
            p.add_argument(
                "--no-artifacts",
                action="store_true",
                help="Skip writing JSON/markdown files to data/eval_wave_reports/.",
            )
        p.set_defaults(func=handler)

    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.preset and not args.manifest:
        print("Error: provide --preset or --manifest", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
