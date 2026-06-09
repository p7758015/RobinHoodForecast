"""Trace odds API → snapshot → scorer (diagnostics only)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from football_agent import config
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.odds_utils import (
    count_odds_fields,
    infer_api_football_season,
    odds_to_debug_dict,
    seasons_to_try,
)
from football_agent.domain.models import Match, Odds
from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder
from football_agent.scorers.league_scorer_v2 import _book_odds_map

logger = logging.getLogger(__name__)

LEAGUE_IDS = config.LEAGUE_IDS_API_FOOTBALL


def trace_odds_for_match(
    match: Match,
    *,
    fd_client=None,
    af_client: Optional[ApiFootballClient] = None,
    season: int = config.CURRENT_SEASON,
) -> Dict[str, Any]:
    """End-to-end odds trace without scoring the full snapshot."""
    league_id = LEAGUE_IDS.get(match.competition_code)
    date_str = match.utc_date.strftime("%Y-%m-%d")
    api_season = infer_api_football_season(match.utc_date)
    try_seasons = seasons_to_try(api_season, season)

    out: Dict[str, Any] = {
        "match": {
            "home": match.home_team.name,
            "away": match.away_team.name,
            "date": date_str,
            "competition": match.competition_code,
        },
        "api_season_inferred": api_season,
        "seasons_tried": try_seasons,
        "fixture_id": None,
        "raw_odds": None,
        "snapshot_odds_context": None,
        "book_odds_map": None,
    }

    if not config.API_FOOTBALL_KEY:
        out["error"] = "API_FOOTBALL_KEY not set"
        return out

    af = af_client or ApiFootballClient(api_key=config.API_FOOTBALL_KEY)
    if league_id is None:
        out["error"] = "unknown league"
        return out

    fixture_id = af.find_fixture_id(
        home_name=match.home_team.name,
        away_name=match.away_team.name,
        date_str=date_str,
        league_id=league_id,
        season=season,
        seasons=try_seasons,
    )
    out["fixture_id"] = fixture_id
    if fixture_id is None:
        out["error"] = "fixture_not_found"
        return out

    odds: Optional[Odds] = af.get_odds(fixture_id)
    out["raw_odds"] = odds_to_debug_dict(odds)
    if odds is None or count_odds_fields(odds) == 0:
        out["warning"] = "no_parsed_markets"

    if fd_client is not None:
        builder = MatchSnapshotBuilder(fd_client, af, season=season)
        snap = builder.build_snapshot(match)
        ctx = snap.odds
        out["snapshot_odds_context"] = {
            "odds_confidence": ctx.odds_confidence,
            "markets": {
                k: (getattr(ctx, k).odds if getattr(ctx, k) is not None else None)
                for k in (
                    "home_win",
                    "away_win",
                    "home_not_lose",
                    "away_not_lose",
                    "btts_yes",
                    "home_team_to_score",
                    "away_team_to_score",
                    "over_15",
                )
            },
        }
        out["book_odds_map"] = _book_odds_map(ctx)

    return out


if __name__ == "__main__":
    from datetime import datetime, timezone

    from football_agent.domain.models import Team

    logging.basicConfig(level=logging.INFO)
    m = Match(
        id=0,
        competition_code="PL",
        home_team=Team(id=73, name="Tottenham Hotspur", short_name="Tottenham"),
        away_team=Team(id=62, name="Everton", short_name="Everton"),
        utc_date=datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc),
        status="SCHEDULED",
        matchday=38,
    )
    report = trace_odds_for_match(m)
    print(json.dumps(report, ensure_ascii=False, indent=2))
