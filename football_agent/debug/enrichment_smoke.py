"""
Smoke / debug CLI for OpenClaw enrichment path.

Usage (when OpenClaw endpoint is available):

  python -m football_agent.debug.enrichment_smoke \\
    --openclaw-url http://localhost:9000 \\
    --home "Arsenal" --away "Chelsea" --date 2026-06-10 \\
    --mode split --verbose

Fixture-only (no Flashscore scraper):

  python -m football_agent.debug.enrichment_smoke \\
    --facts-fixture flashscore_botola_sample_match \\
    --fixtures-dir football_agent/tests/data \\
    --openclaw-url http://localhost:9000 \\
    --json

Not wired into Telegram or production pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from football_agent import config
from football_agent.debug.enrichment_diagnostics import (
    build_smoke_diagnostic,
    format_smoke_summary,
    redact_secrets,
)
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.services.enrichment_live import fetch_enrichment_for_facts

logger = logging.getLogger(__name__)


def _minimal_facts(
    *,
    home: str,
    away: str,
    date_str: Optional[str] = None,
    match_id: str = "",
    match_url: str = "",
    competition: str = "Unknown competition",
) -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id=match_id or "smoke",
            source_url=match_url,
            competition_name=competition,
            home_team_name=home,
            away_team_name=away,
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="smoke_cli"),
    )


def _load_facts_from_fixture(fixtures_dir: Path, stem: str) -> FlashscoreMatchFacts:
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(fixtures_dir),
    ).get_facts_for_match(stem)
    if facts is None:
        raise ValueError(f"Fixture not found: {stem} in {fixtures_dir}")
    return facts


def _load_facts_from_flashscore(
    *,
    fs_url: str,
    match_url: Optional[str],
    match_id: Optional[str],
    home: Optional[str],
    away: Optional[str],
    date_str: Optional[str],
    competition: Optional[str],
    api_key: Optional[str],
) -> tuple[FlashscoreMatchFacts, str]:
    adapter = HttpFlashscoreScraperAdapter(
        fs_url,
        api_key=api_key or config.FLASHSCORE_SCRAPER_API_KEY,
        timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
    )
    service = FlashscoreIngestionService(adapter)
    ref = match_url or match_id
    if ref:
        facts = service.get_facts_for_match(ref)
        if not facts:
            raise RuntimeError("Flashscore returned empty response")
        return facts, "ok"
    if home and away:
        if date_str:
            raw_list = adapter.fetch_matches_for_date(date_str, competition)
            for raw in raw_list:
                facts = service._map_raw_to_facts(raw)  # type: ignore[attr-defined]
                if (
                    home.lower() in facts.meta.home_team_name.lower()
                    and away.lower() in facts.meta.away_team_name.lower()
                ):
                    return facts, "ok"
            raise RuntimeError(f"Match not found for {home} vs {away} on {date_str}")
        return _minimal_facts(home=home, away=away, date_str=date_str), "synthetic"
    raise ValueError("Provide --match-url, --match-id, --facts-fixture, or --home/--away")


def run_smoke(
    *,
    openclaw_url: Optional[str] = None,
    odds_url: Optional[str] = None,
    mode: str = "auto",
    home: Optional[str] = None,
    away: Optional[str] = None,
    date_str: Optional[str] = None,
    competition: Optional[str] = None,
    match_url: Optional[str] = None,
    match_id: Optional[str] = None,
    flashscore_url: Optional[str] = None,
    facts_fixture: Optional[str] = None,
    fixtures_dir: Optional[Path] = None,
    verbose: bool = False,
    openclaw_api_key: Optional[str] = None,
    odds_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    flashscore_status = "not_requested"

    if facts_fixture:
        if not fixtures_dir:
            raise ValueError("--fixtures-dir required with --facts-fixture")
        facts = _load_facts_from_fixture(fixtures_dir, facts_fixture)
        flashscore_status = "fixture"
    elif flashscore_url or config.FLASHSCORE_SCRAPER_URL:
        fs_url = (flashscore_url or config.FLASHSCORE_SCRAPER_URL or "").strip()
        facts, flashscore_status = _load_facts_from_flashscore(
            fs_url=fs_url,
            match_url=match_url,
            match_id=match_id,
            home=home,
            away=away,
            date_str=date_str,
            competition=competition,
            api_key=None,
        )
    elif home and away:
        facts = _minimal_facts(
            home=home,
            away=away,
            date_str=date_str,
            match_id=match_id or "",
            match_url=match_url or "",
            competition=competition or "Unknown competition",
        )
        flashscore_status = "synthetic"
    else:
        raise ValueError(
            "Provide match identity: --facts-fixture, --match-url/--match-id, "
            "or --home/--away (with optional --flashscore-url)",
        )

    result = fetch_enrichment_for_facts(
        facts,
        openclaw_url=openclaw_url,
        openclaw_api_key=openclaw_api_key,
        odds_url=odds_url,
        odds_api_key=odds_api_key,
        home_override=home,
        away_override=away,
        date_override=date_str,
        competition_override=competition,
        match_url_override=match_url,
        mode_override=mode,
    )

    diag = build_smoke_diagnostic(
        facts=facts,
        result=result,
        mode_requested=mode,
        flashscore_status=flashscore_status,
    )
    if verbose:
        diag["verbose"] = {
            "sources": dict(result.sources),
            "routing": {
                "openclaw_base_url": result.routing.openclaw_base_url if result.routing else None,
                "odds_base_url": result.routing.odds_base_url if result.routing else None,
                "odds_separate_service": result.routing.odds_separate_service if result.routing else None,
                "openclaw_provides_odds": result.routing.openclaw_provides_odds if result.routing else None,
            },
            "context_meta": (
                result.context.meta.model_dump(mode="json") if result.context else None
            ),
            "odds_meta": result.odds.meta.model_dump(mode="json") if result.odds else None,
        }
    return diag


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enrichment_smoke",
        description="Smoke-test OpenClaw enrichment (context + odds) against real or configured backend.",
    )
    parser.add_argument("--openclaw-url", help="Override OPENCLAW_BASE_URL.")
    parser.add_argument("--openclaw-api-key", help="Override API key (not printed in output).")
    parser.add_argument("--odds-url", help="Override ODDS_SERVICE_URL (legacy separate service).")
    parser.add_argument("--mode", choices=("auto", "split", "unified"), default="auto")
    parser.add_argument("--match-url", help="Flashscore match URL.")
    parser.add_argument("--match-id", help="Flashscore match id.")
    parser.add_argument("--home", help="Home team (synthetic or team/date lookup).")
    parser.add_argument("--away", help="Away team.")
    parser.add_argument("--date", help="Match date YYYY-MM-DD.")
    parser.add_argument("--competition", help="Competition code filter.")
    parser.add_argument("--flashscore-url", help="Override FLASHSCORE_SCRAPER_URL.")
    parser.add_argument("--facts-fixture", help="Fixture stem under --fixtures-dir (offline facts).")
    parser.add_argument("--fixtures-dir", help="Directory with flashscore fixture JSON.")
    parser.add_argument("--verbose", action="store_true", help="Include structured diagnostic details.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON only.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    try:
        diag = run_smoke(
            openclaw_url=args.openclaw_url,
            odds_url=args.odds_url,
            mode=args.mode,
            home=args.home,
            away=args.away,
            date_str=args.date,
            competition=args.competition,
            match_url=args.match_url,
            match_id=args.match_id,
            flashscore_url=args.flashscore_url,
            facts_fixture=args.facts_fixture,
            fixtures_dir=Path(args.fixtures_dir) if args.fixtures_dir else None,
            verbose=args.verbose,
            openclaw_api_key=args.openclaw_api_key,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("Smoke run failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_smoke_summary(diag))
        if args.verbose:
            print("\n--- verbose diagnostic ---")
            payload = json.dumps(diag, ensure_ascii=False, indent=2, default=str)
            print(redact_secrets(payload))

    status = diag.get("status") or {}
    completeness = status.get("completeness")
    if completeness == "not_configured":
        return 0
    if completeness == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
