"""
Normalize and assess Flashscore HTTP scraper payloads before facts mapping.

No fabricated data — only reshape fields the scraper already provides and
record honest completeness signals.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

BLOCK_KEYS = (
    "standings",
    "season_context",
    "form",
    "h2h",
    "squad_raw",
    "schedule_raw",
    "stats_raw",
)


def _normalize_form_token(token: Any) -> Optional[str]:
    if token is None:
        return None
    t = str(token).strip().upper()
    if t in ("W", "WIN"):
        return "W"
    if t in ("D", "DRAW"):
        return "D"
    if t in ("L", "LOSS", "LOSE"):
        return "L"
    if len(t) == 1 and t in "WDL":
        return t
    return None


def _form_results_from_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            tok = _normalize_form_token(item)
            if tok:
                out.append(tok)
        elif isinstance(item, dict):
            r = item.get("result") or item.get("outcome")
            tok = _normalize_form_token(r)
            if tok:
                out.append(tok)
    return out


def standings_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if (data.get("home_position") or 0) > 0 or (data.get("away_position") or 0) > 0:
        return True
    if (data.get("home_matches_played") or 0) > 0 or (data.get("away_matches_played") or 0) > 0:
        return True
    if (data.get("home_points") or 0) > 0 or (data.get("away_points") or 0) > 0:
        return True
    return False


def season_context_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if (data.get("matchday_number") or 0) > 0:
        return True
    if (data.get("total_matchdays") or 0) > 0:
        return True
    neighbors = data.get("table_neighbors") or {}
    return isinstance(neighbors, dict) and any(v for v in neighbors.values() if v)


def form_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict):
        return False
    for side in ("home", "away"):
        block = data.get(side) or {}
        if not isinstance(block, dict):
            continue
        if block.get("last_n_results"):
            return True
        if (block.get("last_n_points") or 0) > 0:
            return True
        if (block.get("goals_for_last_n") or 0) > 0:
            return True
    return False


def h2h_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if (data.get("recent_h2h_matches") or 0) > 0:
        return True
    total = (
        (data.get("home_h2h_wins") or 0)
        + (data.get("away_h2h_wins") or 0)
        + (data.get("h2h_draws") or 0)
    )
    return total > 0


def squad_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    lineups = data.get("predicted_lineups") or data.get("confirmed_lineups") or {}
    if isinstance(lineups, dict) and any(lineups.get(s) for s in ("home", "away")):
        return True
    missing = data.get("missing_players_raw") or {}
    if isinstance(missing, dict) and any(missing.get(s) for s in ("home", "away")):
        return True
    status = data.get("player_status_raw") or {}
    if isinstance(status, dict) and any(
        isinstance(v, dict) and v for v in status.values()
    ):
        return True
    bench = data.get("bench") or {}
    if isinstance(bench, dict) and any(bench.get(s) for s in ("home", "away")):
        return True
    for coach_key in ("coach_name_home", "coach_name_away"):
        name = str(data.get(coach_key) or "").strip()
        if name and name.lower() != "unknown":
            return True
    return False


def schedule_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    for key in (
        "previous_match_date_home",
        "previous_match_date_away",
        "next_match_date_home",
        "next_match_date_away",
    ):
        if data.get(key):
            return True
    for key in ("recent_match_dates_home", "recent_match_dates_away"):
        if data.get(key):
            return True
    return False


def stats_has_signal(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    team_stats = data.get("team_stats") or {}
    if isinstance(team_stats, dict):
        for side in ("home", "away"):
            side_stats = team_stats.get(side) or {}
            if isinstance(side_stats, dict) and any(v for v in side_stats.values() if v):
                return True
    if data.get("match_stats") or data.get("incidents"):
        return True
    return False


def assess_block_signals(raw: Dict[str, Any]) -> Dict[str, bool]:
    return {
        "standings": standings_has_signal(raw.get("standings")),
        "season_context": season_context_has_signal(raw.get("season_context")),
        "form": form_has_signal(raw.get("form")),
        "h2h": h2h_has_signal(raw.get("h2h")),
        "squad_raw": squad_has_signal(raw.get("squad_raw")),
        "schedule_raw": schedule_has_signal(raw.get("schedule_raw")),
        "stats_raw": stats_has_signal(raw.get("stats_raw")),
    }


def _aggregate_h2h_list(items: List[Any]) -> Dict[str, Any]:
    home_wins = away_wins = draws = 0
    goals: List[float] = []
    btts = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        score = str(item.get("score") or item.get("result") or "")
        m = re.match(r"(\d+)\s*[-:]\s*(\d+)", score)
        if not m:
            continue
        hg, ag = int(m.group(1)), int(m.group(2))
        goals.append(hg + ag)
        if hg > 0 and ag > 0:
            btts += 1
        if hg > ag:
            home_wins += 1
        elif ag > hg:
            away_wins += 1
        else:
            draws += 1
    count = home_wins + away_wins + draws
    return {
        "recent_h2h_matches": count,
        "home_h2h_wins": home_wins,
        "away_h2h_wins": away_wins,
        "h2h_draws": draws,
        "avg_h2h_goals": (sum(goals) / len(goals)) if goals else None,
        "btts_h2h_rate": (btts / count) if count else None,
    }


def enrich_http_flashscore_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand partial HTTP scraper dict into agent contract shape.

    Preserves existing nested blocks; maps known alternate keys only.
    """
    out = dict(raw)
    warnings: List[str] = list(out.get("enrichment_warnings") or [])

    if out.get("competition") and not out.get("competition_name"):
        out["competition_name"] = out["competition"]
    if out.get("home_team") and not out.get("home_team_name"):
        out["home_team_name"] = out["home_team"]
    if out.get("away_team") and not out.get("away_team_name"):
        out["away_team_name"] = out["away_team"]
    if out.get("url") and not out.get("source_url"):
        out["source_url"] = out["url"]

    # form: home_form / away_form list → form block
    if not form_has_signal(out.get("form")):
        home_results = _form_results_from_list(out.get("home_form"))
        away_results = _form_results_from_list(out.get("away_form"))
        if home_results or away_results:
            out["form"] = {
                "home": {"last_n_results": home_results},
                "away": {"last_n_results": away_results},
            }
            warnings.append("form_mapped_from_home_away_form_lists")

    # h2h: list of matches → aggregate block
    h2h_val = out.get("h2h")
    if isinstance(h2h_val, list) and h2h_val:
        out["h2h"] = _aggregate_h2h_list(h2h_val)
        warnings.append("h2h_aggregated_from_match_list")

    # standings: flat rank fields on root
    if not standings_has_signal(out.get("standings")):
        if out.get("home_position") or out.get("away_position"):
            out["standings"] = {
                "home_position": out.get("home_position"),
                "away_position": out.get("away_position"),
                "home_points": out.get("home_points"),
                "away_points": out.get("away_points"),
            }
            warnings.append("standings_mapped_from_flat_fields")

    # season_context from round/matchday hints
    if not season_context_has_signal(out.get("season_context")):
        md = out.get("matchday_number") or out.get("round")
        if md is not None:
            try:
                md_int = int(str(md).strip())
                if md_int > 0:
                    out["season_context"] = {
                        "matchday_number": md_int,
                        "total_matchdays": out.get("total_matchdays"),
                    }
                    warnings.append("season_context_from_round_metadata")
            except ValueError:
                pass

    signals = assess_block_signals(out)
    missing = [k for k, ok in signals.items() if not ok]
    if missing:
        warnings.append(f"flashscore_blocks_missing:{','.join(missing)}")

    meta = out.get("blocks_meta")
    if isinstance(meta, dict):
        for block, info in meta.items():
            if isinstance(info, dict) and info.get("present") is False:
                warnings.append(f"scraper_reported_missing:{block}")

    out["enrichment_warnings"] = warnings
    return out
