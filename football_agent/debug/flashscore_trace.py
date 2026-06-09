"""
Flashscore league smoke/debug trace for normalized facts.

This tool:
- reads raw Flashscore payloads via a fixture-based adapter
- maps them into FlashscoreMatchFacts
- prints compact completeness summary per match

It does NOT call v2 snapshots / scorers / OpenClaw / legacy APIs.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.derived_season import LeagueTableMotivationContext, derive_season_motivation
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.service import FlashscoreIngestionService

logger = logging.getLogger(__name__)


def _blocks_flags(facts: FlashscoreMatchFacts) -> Dict[str, str]:
    def flag(val: object) -> str:
        return "yes" if val is not None else "missing"

    return {
        "standings": flag(facts.standings),
        "season_context_inputs": flag(facts.season_context_inputs),
        "form": flag(facts.form),
        "h2h": flag(facts.h2h),
        "squad_raw": flag(facts.squad_raw),
        "schedule_raw": flag(facts.schedule_raw),
        "stats_raw": flag(facts.stats_raw),
    }


def build_facts_summary(facts: FlashscoreMatchFacts) -> Dict[str, Any]:
    m = facts.meta
    blocks = _blocks_flags(facts)
    st = facts.standings
    form = facts.form
    h2h = facts.h2h
    prov = facts.provenance

    def form_side(side: str) -> Dict[str, Any]:
        tb = getattr(form, side) if form else None
        if tb is None:
            return {}
        return {
            "last_n_results": tb.last_n_results,
            "last_n_points": tb.last_n_points,
            "btts_last_n": tb.btts_last_n,
            "over_25_last_n": tb.over_25_last_n,
        }

    derived: LeagueTableMotivationContext = derive_season_motivation(facts)

    return {
        "meta": {
            "match_id": m.match_id,
            "source_url": m.source_url,
            "competition_name": m.competition_name,
            "competition_country": m.competition_country,
            "season": m.season,
            "tournament_type": m.tournament_type,
            "stage": m.stage,
            "round": m.round,
            "kickoff_utc": m.kickoff_utc.isoformat() if m.kickoff_utc else None,
            "home_team_name": m.home_team_name,
            "away_team_name": m.away_team_name,
            "status": m.status,
        },
        "blocks": blocks,
        "standings_summary": (
            {
                "home_position": st.home_position,
                "away_position": st.away_position,
                "home_points": st.home_points,
                "away_points": st.away_points,
                "home_goal_difference": st.home_goal_difference,
                "away_goal_difference": st.away_goal_difference,
            }
            if st
            else None
        ),
        "form_summary": {
            "home": form_side("home"),
            "away": form_side("away"),
        },
        "h2h_summary": (
            {
                "recent_h2h_matches": h2h.recent_h2h_matches,
                "home_h2h_wins": h2h.home_h2h_wins,
                "away_h2h_wins": h2h.away_h2h_wins,
                "h2h_draws": h2h.h2h_draws,
                "btts_h2h_rate": h2h.btts_h2h_rate,
            }
            if h2h
            else None
        ),
        "derived_season_motivation": {
            "season_phase": derived.season_phase,
            "rounds_remaining_after_this_match": derived.rounds_remaining_after_this_match,
            "gap_to_title_points": derived.gap_to_title_points,
            "gap_to_europe_points": derived.gap_to_europe_points,
            "gap_to_relegation_safety_points": derived.gap_to_relegation_safety_points,
            "home_target_band": derived.home_target_band,
            "away_target_band": derived.away_target_band,
            "urgency_level_home": derived.urgency_level_home,
            "urgency_level_away": derived.urgency_level_away,
            "home_mathematical_title_alive": derived.home_mathematical_title_alive,
            "away_mathematical_title_alive": derived.away_mathematical_title_alive,
            "home_mathematical_europe_alive": derived.home_mathematical_europe_alive,
            "away_mathematical_europe_alive": derived.away_mathematical_europe_alive,
            "home_mathematical_relegation_risk_alive": derived.home_mathematical_relegation_risk_alive,
            "away_mathematical_relegation_risk_alive": derived.away_mathematical_relegation_risk_alive,
            "points_gap_home_to_title": derived.points_gap_home_to_title,
            "points_gap_away_to_title": derived.points_gap_away_to_title,
            "points_gap_home_to_europe": derived.points_gap_home_to_europe,
            "points_gap_away_to_europe": derived.points_gap_away_to_europe,
            "points_gap_home_to_relegation_line": derived.points_gap_home_to_relegation_line,
            "points_gap_away_to_relegation_line": derived.points_gap_away_to_relegation_line,
            "aux_gap_to_title_positions": derived.aux_gap_to_title_positions,
            "aux_gap_to_europe_positions": derived.aux_gap_to_europe_positions,
            "aux_gap_to_relegation_line_positions": derived.aux_gap_to_relegation_line_positions,
            "derivation_warnings": list(derived.derivation_warnings),
        },
        "provenance": {
            "scraper_backend_name": prov.scraper_backend_name,
            "scraper_backend_version": prov.scraper_backend_version,
            "adapter_version": prov.adapter_version,
            "collected_at_utc": prov.collected_at_utc.isoformat() if prov.collected_at_utc else None,
            "blocks_present": prov.blocks_present,
            "missing_blocks": prov.missing_blocks,
            "parsing_warnings": prov.parsing_warnings,
        },
    }


def _print_summary(summary: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    meta = summary["meta"]
    blocks = summary["blocks"]
    derived = summary.get("derived_season_motivation") or {}

    print("Flashscore facts debug summary")
    print(f"- {meta['competition_name']} ({meta.get('competition_country')}) / season={meta.get('season')}")
    print(f"- {meta.get('tournament_type')} / stage={meta.get('stage')} / round={meta.get('round')}")
    print(f"- {meta.get('kickoff_utc')}  {meta['home_team_name']} — {meta['away_team_name']}  [{meta.get('status')}]")
    print("")
    print("Blocks:")
    for name in ("standings", "season_context_inputs", "form", "h2h", "squad_raw", "schedule_raw", "stats_raw"):
        print(f"- {name}: {blocks[name]}")

    if summary.get("standings_summary"):
        ss = summary["standings_summary"]
        print("")
        print("Standings:")
        print(
            f"- home: pos={ss.get('home_position')} pts={ss.get('home_points')} gd={ss.get('home_goal_difference')}"
        )
        print(
            f"- away: pos={ss.get('away_position')} pts={ss.get('away_points')} gd={ss.get('away_goal_difference')}"
        )

    if derived:
        print("")
        print("Derived season motivation:")
        print(f"- season_phase: {derived.get('season_phase')}, rounds_remaining: {derived.get('rounds_remaining_after_this_match')}")
        print(
            f"- gaps(points): title={derived.get('gap_to_title_points')} "
            f"europe={derived.get('gap_to_europe_points')} "
            f"safety={derived.get('gap_to_relegation_safety_points')}"
        )
        print(
            f"- home_band={derived.get('home_target_band')} away_band={derived.get('away_target_band')}"
        )
        print(
            f"- urgency_home={derived.get('urgency_level_home')} urgency_away={derived.get('urgency_level_away')}"
        )
        if derived.get("derivation_warnings"):
            print(f"- derivation_warnings: {derived.get('derivation_warnings')}")

    if summary.get("provenance"):
        p = summary["provenance"]
        print("")
        print("Provenance:")
        print(f"- backend: {p.get('scraper_backend_name')} v{p.get('scraper_backend_version')}")
        print(f"- adapter: {p.get('adapter_version')} collected_at={p.get('collected_at_utc')}")
        print(f"- blocks_present: {p.get('blocks_present')}")
        print(f"- missing_blocks: {p.get('missing_blocks')}")
        if p.get("parsing_warnings"):
            print(f"- parsing_warnings: {p.get('parsing_warnings')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flashscore_trace",
        description="Debug normalized Flashscore facts from fixture-based backend.",
    )
    parser.add_argument("--fixtures-dir", type=str, required=True, help="Directory with Flashscore raw JSON fixtures.")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD for batch mode.")
    parser.add_argument("--competition", type=str, help="Optional competition code (e.g. SA, PL).")
    parser.add_argument("--match-id", type=str, help="Single match id / filename stem.")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text.")
    parser.add_argument("--max", type=int, default=3, help="Max matches to show for date listing.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.exists():
        logger.error("Fixtures dir %s does not exist", fixtures_dir)
        return 2

    service = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(fixtures_dir))

    if args.match_id:
        facts = service.get_facts_for_match(args.match_id)
        if not facts:
            print("Match not found in fixtures.")
            return 1
        summary = build_facts_summary(facts)
        _print_summary(summary, as_json=args.json)
        return 0

    if not args.date:
        logger.error("Either --match-id or --date must be provided.")
        return 2

    facts_list: List[FlashscoreMatchFacts] = service.get_facts_for_date(args.date, competition_code=args.competition)
    if not facts_list:
        print(f"No matches found in fixtures for date={args.date} competition={args.competition or 'ALL'}")
        return 0

    limit = max(1, int(args.max))
    for facts in facts_list[:limit]:
        summary = build_facts_summary(facts)
        _print_summary(summary, as_json=args.json)
        print("\n" + "-" * 72 + "\n")

    if len(facts_list) > limit:
        print(f"... truncated: {len(facts_list)} matches total (use --max to increase)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

