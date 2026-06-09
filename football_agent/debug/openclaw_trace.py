"""
OpenClaw league smoke/debug trace.

Goals:
- Verify OpenClaw match discovery (by date / competition).
- Inspect raw payload coverage (without dumping huge JSON).
- Inspect snapshot completeness + odds markets + confidence.
- Run existing LeagueScorerV2 and show best/top markets.

This module intentionally does NOT use legacy Football-Data ingestion.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from football_agent import config
from football_agent.domain.enums_v2 import TournamentType
from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2, MatchPredictionResultV2
from football_agent.openclaw.adapter import OpenClawSnapshotBuilder
from football_agent.openclaw.client import OpenClawClient, OpenClawConfigurationError
from football_agent.openclaw.models import OpenClawMatchPayload
from football_agent.openclaw.service import OpenClawLeagueAnalysisService
from football_agent.output.market_display import format_market_pick

logger = logging.getLogger(__name__)


FIRST_BATCH_MARKET_KEYS: Tuple[str, ...] = (
    "HOME_WIN",
    "AWAY_WIN",
    "HOME_NOT_LOSE",
    "AWAY_NOT_LOSE",
    "BTTS_YES",
    "HOME_TEAM_TO_SCORE",
    "AWAY_TEAM_TO_SCORE",
    "OVER_1_5",
)


def _present_flag(ok: bool, *, partial: bool = False) -> str:
    if ok:
        return "yes"
    if partial:
        return "partial"
    return "missing"


def _odds_market_count(snapshot: MatchAnalysisSnapshotV2) -> Tuple[int, int, List[str]]:
    odds = snapshot.odds
    present: Dict[str, bool] = {
        "HOME_WIN": odds.home_win is not None,
        "AWAY_WIN": odds.away_win is not None,
        "HOME_NOT_LOSE": odds.home_not_lose is not None,
        "AWAY_NOT_LOSE": odds.away_not_lose is not None,
        "BTTS_YES": odds.btts_yes is not None,
        "HOME_TEAM_TO_SCORE": odds.home_team_to_score is not None,
        "AWAY_TEAM_TO_SCORE": odds.away_team_to_score is not None,
        "OVER_1_5": odds.over_15 is not None,
    }
    missing = [k for k, v in present.items() if not v]
    return sum(1 for v in present.values() if v), len(present), missing


def _raw_keys_summary(payload: OpenClawMatchPayload) -> Dict[str, Any]:
    """Compact raw summary: only presence and a few counts."""
    d = payload.model_dump(exclude_none=True)
    return {
        "schema_version": d.get("schema_version"),
        "source": {
            "source_name": (d.get("source") or {}).get("source_name"),
            "tags": (d.get("source") or {}).get("tags") or [],
            "freshness": (d.get("source") or {}).get("data_freshness_score"),
            "completeness": (d.get("source") or {}).get("completeness_score"),
            "confidence": (d.get("source") or {}).get("confidence_score"),
        }
        if d.get("source")
        else None,
        "meta": {
            "competition_code": (d.get("meta") or {}).get("competition_code"),
            "competition_name": (d.get("meta") or {}).get("competition_name"),
            "tournament_type": (d.get("meta") or {}).get("tournament_type"),
            "match_date_utc": (d.get("meta") or {}).get("match_date_utc"),
            "round_number": (d.get("meta") or {}).get("round_number"),
            "country": (d.get("meta") or {}).get("country"),
        }
        if d.get("meta")
        else None,
        "blocks_present": {
            "home_context": bool(d.get("home_context")),
            "away_context": bool(d.get("away_context")),
            "home_squad": bool(d.get("home_squad")),
            "away_squad": bool(d.get("away_squad")),
            "home_coach": bool(d.get("home_coach")),
            "away_coach": bool(d.get("away_coach")),
            "home_schedule": bool(d.get("home_schedule")),
            "away_schedule": bool(d.get("away_schedule")),
            "odds": bool(d.get("odds")),
            "h2h": bool(d.get("h2h")),
            "news": bool(d.get("news")),
        },
    }


def snapshot_completeness_summary(snapshot: MatchAnalysisSnapshotV2) -> Dict[str, Any]:
    """Summarize presence for the blocks that league blueprint expects."""
    meta = snapshot.match_meta
    home = snapshot.home_team_context
    away = snapshot.away_team_context

    odds_present, odds_total, odds_missing = _odds_market_count(snapshot)

    blocks = {
        "form": _present_flag(
            (home.form.last_5_form_score != 0.5)
            or (away.form.last_5_form_score != 0.5)
            or (home.form.last_10_form_score != 0.5)
            or (away.form.last_10_form_score != 0.5),
            partial=False,
        ),
        "motivation": _present_flag(
            (home.motivation.motivation_score != 0.5)
            or (away.motivation.motivation_score != 0.5)
            or (home.motivation.league_position is not None)
            or (away.motivation.league_position is not None),
            partial=False,
        ),
        "schedule": _present_flag(
            (snapshot.home_schedule.days_since_last_match is not None)
            or (snapshot.away_schedule.days_since_last_match is not None)
            or (snapshot.home_schedule.matches_last_14_days != 0)
            or (snapshot.away_schedule.matches_last_14_days != 0)
            or (home.schedule.fixture_congestion_score != 0.0)
            or (away.schedule.fixture_congestion_score != 0.0),
            partial=False,
        ),
        "squad": _present_flag(
            bool(snapshot.home_squad.missing_players)
            or bool(snapshot.away_squad.missing_players)
            or bool(snapshot.home_squad.suspended_players)
            or bool(snapshot.away_squad.suspended_players)
            or bool(snapshot.home_squad.doubtful_players)
            or bool(snapshot.away_squad.doubtful_players)
            or (snapshot.home_squad.starting_xi_confidence != 0.5)
            or (snapshot.away_squad.starting_xi_confidence != 0.5),
            partial=False,
        ),
        "coach": _present_flag(
            (snapshot.home_coach.coach.name != "Unknown") or (snapshot.away_coach.coach.name != "Unknown"),
            partial=False,
        ),
        "h2h": _present_flag(snapshot.h2h_context.team_h2h_total_matches > 0, partial=False),
        "news": _present_flag(
            bool(snapshot.news_context.major_news_items)
            or bool(snapshot.news_context.priority_signals)
            or bool(snapshot.news_context.rotation_signals)
            or bool(snapshot.news_context.locker_room_issues),
            partial=False,
        ),
        "odds": f"{odds_present}/{odds_total} markets",
    }

    conf = snapshot.confidence
    conf_summary = {
        "overall": conf.overall_confidence_score,
        "completeness": conf.overall_completeness_score,
        "freshness": conf.data_freshness_score,
        "odds_conf": conf.odds_confidence,
        "teams_conf": conf.teams_confidence,
        "squads_conf": conf.squads_confidence,
        "coaches_conf": conf.coaches_confidence,
        "schedule_conf": conf.schedule_confidence,
        "h2h_conf": conf.h2h_confidence,
        "news_conf": conf.news_confidence,
    }

    return {
        "match": {
            "competition": meta.competition_code,
            "competition_name": meta.competition_name,
            "tournament_type": meta.tournament_type,
            "date_utc": meta.match_date_utc.isoformat(),
            "home": meta.home_team.short_name or meta.home_team.name,
            "away": meta.away_team.short_name or meta.away_team.name,
        },
        "source_tags": list(snapshot.source_tags),
        "blocks": blocks,
        "odds_missing": odds_missing,
        "confidence": conf_summary,
    }


def score_summary(result: MatchPredictionResultV2) -> Dict[str, Any]:
    best = result.best_market
    top = sorted(result.market_predictions, key=lambda m: m.probability, reverse=True)[:5]

    return {
        "best_market": {
            "market_key": best.market_key,
            "probability": best.probability,
            "book_odds": best.book_odds,
            "edge": best.edge,
            "display": format_market_pick(best.market_key, best.probability, best.book_odds, label=best.label),
        }
        if best
        else None,
        "top_markets": [
            {
                "market_key": m.market_key,
                "probability": m.probability,
                "book_odds": m.book_odds,
                "edge": m.edge,
                "display": format_market_pick(m.market_key, m.probability, m.book_odds, label=m.label),
            }
            for m in top
        ],
        "express_allow": getattr(result, "express_safety", None).allow_for_express
        if getattr(result, "express_safety", None) is not None
        else None,
    }


def build_trace_report(payload: OpenClawMatchPayload) -> Dict[str, Any]:
    builder = OpenClawSnapshotBuilder()
    snapshot = builder.build(payload)

    result = OpenClawLeagueAnalysisService(fetch_matches_fn=lambda *_: []).analyze_from_payload(payload)

    non_league = snapshot.match_meta.tournament_type != TournamentType.LEAGUE_REGULAR

    return {
        "using_openclaw_path": True,
        "non_league_warning": non_league,
        "raw_summary": _raw_keys_summary(payload),
        "snapshot_summary": snapshot_completeness_summary(snapshot),
        "score_summary": score_summary(result),
    }


def _load_payloads_for_date(
    date_str: str,
    competition_code: Optional[str],
) -> List[OpenClawMatchPayload]:
    client = OpenClawClient()
    blobs = client.fetch_matches_payloads_for_date(date_str, competition_code=competition_code)
    return list(blobs)


def _pick_payload_by_teams(
    payloads: Sequence[OpenClawMatchPayload],
    home: str,
    away: str,
) -> Tuple[Optional[OpenClawMatchPayload], Optional[str]]:
    # reuse resolution over scored results
    svc = OpenClawLeagueAnalysisService(fetch_matches_fn=lambda *_: [p.model_dump() for p in payloads])
    pred, err = svc.analyze_match_by_teams(home, away, "unused")
    if err:
        return None, err
    if not pred:
        return None, "Матч не найден."

    target_id = pred.match_meta.match_id
    for p in payloads:
        snap = OpenClawSnapshotBuilder().build(p)
        if snap.match_meta.match_id == target_id:
            return p, None
    return None, "Матч найден по скорингу, но не сопоставлен с payload (unexpected)."


def _print_report(report: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    raw = report.get("raw_summary") or {}
    snap = report.get("snapshot_summary") or {}
    score = report.get("score_summary") or {}

    m = (snap.get("match") or {}) if isinstance(snap, dict) else {}

    print("OpenClaw trace (league smoke)")
    print(f"- using_openclaw_path: {report.get('using_openclaw_path')}")
    if report.get("non_league_warning"):
        print("- WARNING: non-league match detected; league expectations may not apply")

    print("")
    print("Match")
    print(f"- {m.get('competition')} / {m.get('competition_name')} / {m.get('tournament_type')}")
    print(f"- {m.get('date_utc')}")
    print(f"- {m.get('home')} — {m.get('away')}")

    print("")
    print("Raw payload")
    src = raw.get("source") or {}
    if src:
        print(f"- source: {src.get('source_name')}")
        print(f"- tags: {src.get('tags')}")
        print(f"- freshness: {src.get('freshness')} completeness: {src.get('completeness')} confidence: {src.get('confidence')}")
    blocks_present = raw.get("blocks_present") or {}
    if blocks_present:
        present_keys = [k for k, v in blocks_present.items() if v]
        print(f"- blocks_present: {present_keys}")

    print("")
    print("Snapshot completeness")
    blocks = snap.get("blocks") or {}
    for k in ("form", "motivation", "schedule", "squad", "coach", "h2h", "news", "odds"):
        if k in blocks:
            print(f"- {k}: {blocks[k]}")
    if snap.get("odds_missing"):
        print(f"- odds missing: {snap.get('odds_missing')}")

    conf = snap.get("confidence") or {}
    if conf:
        print("")
        print("Confidence")
        print(
            f"- overall: {conf.get('overall')} completeness: {conf.get('completeness')} freshness: {conf.get('freshness')}"
        )
        print(
            f"- odds_conf: {conf.get('odds_conf')} teams_conf: {conf.get('teams_conf')} squads_conf: {conf.get('squads_conf')}"
        )

    print("")
    print("Scorer")
    bm = score.get("best_market")
    if bm:
        print(f"- best_market: {bm.get('display')}")
    top = score.get("top_markets") or []
    if top:
        print("- top_markets:")
        for i, t in enumerate(top[:5], start=1):
            print(f"  {i}. {t.get('display')}")
    if score.get("express_allow") is not None:
        print(f"- express_allow: {score.get('express_allow')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="openclaw_trace", description="OpenClaw league smoke/debug trace")
    parser.add_argument("--date", help="YYYY-MM-DD", required=False)
    parser.add_argument("--competition", help="Competition code e.g. SA/PL", required=False)
    parser.add_argument("--home", help="Home team query (optional)", required=False)
    parser.add_argument("--away", help="Away team query (optional)", required=False)
    parser.add_argument("--json", action="store_true", help="Print as JSON")
    parser.add_argument("--max", type=int, default=3, help="Max matches to print for date list")

    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not config.OPENCLAW_BASE_URL:
        logger.error("OPENCLAW_BASE_URL is not set. This tool only runs on OpenClaw.")
        return 2

    if not args.date:
        logger.error("--date is required for now (match discovery is date-based).")
        return 2

    try:
        payloads = _load_payloads_for_date(args.date, args.competition)
    except OpenClawConfigurationError as e:
        logger.error(str(e))
        return 2

    if not payloads:
        print(f"No OpenClaw matches found for date={args.date} competition={args.competition or 'ALL'}")
        return 0

    if args.home and args.away:
        payload, err = _pick_payload_by_teams(payloads, args.home, args.away)
        if err:
            print(err)
            return 1
        assert payload is not None
        report = build_trace_report(payload)
        _print_report(report, as_json=args.json)
        return 0

    for p in payloads[: max(1, int(args.max))]:
        report = build_trace_report(p)
        _print_report(report, as_json=args.json)
        print("\n" + "-" * 72 + "\n")

    if len(payloads) > args.max:
        print(f"... truncated: {len(payloads)} matches total (use --max to increase)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

