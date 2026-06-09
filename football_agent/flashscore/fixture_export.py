"""
Convert HTTP / partial Flashscore raw payloads into fixture-compatible JSON records.

Debug/offline only — matches ``football_agent/tests/data/*.json`` shape used by
``FixtureFileFlashscoreAdapter`` and ``batch-persist`` flows.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

_EMPTY_FORM_SIDE = {
    "last_n_results": [],
    "last_n_points": 0,
    "goals_for_last_n": 0,
    "goals_against_last_n": 0,
    "clean_sheets_last_n": 0,
    "btts_last_n": 0,
    "over_25_last_n": 0,
}

_FIXTURE_BLOCK_DEFAULTS: Dict[str, Any] = {
    "standings": {
        "home_position": 0,
        "away_position": 0,
        "home_points": 0,
        "away_points": 0,
        "home_matches_played": 0,
        "away_matches_played": 0,
        "home_goal_difference": 0,
        "away_goal_difference": 0,
    },
    "season_context": {
        "matchday_number": 0,
        "total_matchdays": 0,
        "rounds_remaining_after_this_match": 0,
        "table_neighbors": {
            "ucl_cutoff_pos": 0,
            "relegation_cutoff_pos": 0,
            "title_leader_points": 0,
            "ucl_cutoff_points": 0,
            "relegation_safety_points": 0,
        },
    },
    "form": {
        "home": copy.deepcopy(_EMPTY_FORM_SIDE),
        "away": copy.deepcopy(_EMPTY_FORM_SIDE),
    },
    "h2h": {
        "recent_h2h_matches": 0,
        "home_h2h_wins": 0,
        "away_h2h_wins": 0,
        "h2h_draws": 0,
        "avg_h2h_goals": 0.0,
        "btts_h2h_rate": 0.0,
    },
    "squad_raw": {
        "predicted_lineups": {"home": [], "away": []},
        "coach_name_home": "Unknown",
        "coach_name_away": "Unknown",
    },
    "schedule_raw": {
        "previous_match_date_home": None,
        "previous_match_date_away": None,
        "recent_match_dates_home": [],
        "recent_match_dates_away": [],
    },
    "stats_raw": {
        "team_stats": {
            "home": {"avg_goals_for": 0.0},
            "away": {"avg_goals_for": 0.0},
        },
    },
}


def extract_match_id_from_url(url: str) -> Optional[str]:
    """Parse Flashscore ``mid`` query param or last meaningful path segment."""
    text = (url or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    qs = parse_qs(parsed.query)
    if "mid" in qs and qs["mid"]:
        return str(qs["mid"][0]).strip() or None
    parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(parts):
        if part not in ("match", "football") and len(part) >= 6:
            return part
    return None


def _normalize_kickoff_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            return value.strip()
    return None


def _kickoff_date_stub(kickoff_iso: Optional[str]) -> Optional[str]:
    if not kickoff_iso or len(kickoff_iso) < 10:
        return None
    return kickoff_iso[:10]


def default_fixture_stem(record: Dict[str, Any]) -> str:
    match_id = str(record.get("match_id") or "unknown").strip() or "unknown"
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", match_id).strip("_")
    return f"flashscore_{safe}"


def coerce_raw_to_fixture_record(
    raw: Dict[str, Any],
    *,
    allow_stubs: bool = True,
) -> Dict[str, Any]:
    """
    Normalize a scraper raw dict into ``tests/data``-compatible fixture JSON.

    Preserves nested blocks when already present; fills validator-friendly stubs
    only when ``allow_stubs=True`` (debug export). Live ingestion must not use stubs.
    """
    from football_agent.flashscore.raw_enrich import assess_block_signals, enrich_http_flashscore_raw

    raw = enrich_http_flashscore_raw(raw)
    source_url = str(raw.get("source_url") or raw.get("url") or "").strip()
    match_id = str(raw.get("match_id") or raw.get("id") or extract_match_id_from_url(source_url) or "").strip()
    home = str(raw.get("home_team_name") or raw.get("home_team") or "").strip()
    away = str(raw.get("away_team_name") or raw.get("away_team") or "").strip()
    kickoff_iso = _normalize_kickoff_iso(raw.get("kickoff_utc") or raw.get("date"))

    tournament_type = raw.get("tournament_type") or "LEAGUE_REGULAR"
    if hasattr(tournament_type, "name"):
        tournament_type = tournament_type.name
    tournament_type = str(tournament_type)

    out: Dict[str, Any] = {
        "match_id": match_id,
        "source_url": source_url,
        "competition_name": str(
            raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or "Unknown competition"
        ),
        "competition_country": raw.get("competition_country"),
        "season": raw.get("season"),
        "stage": raw.get("stage"),
        "round": raw.get("round"),
        "tournament_type": tournament_type,
        "kickoff_utc": kickoff_iso,
        "home_team_name": home,
        "away_team_name": away,
        "status": str(raw.get("status") or "SCHEDULED"),
        "scraper_backend_name": str(raw.get("scraper_backend_name") or "http"),
        "scraper_backend_version": raw.get("scraper_backend_version"),
    }

    if raw.get("collected_at_utc"):
        out["collected_at_utc"] = raw.get("collected_at_utc")

    signals = assess_block_signals(raw)
    for block_name, default_block in _FIXTURE_BLOCK_DEFAULTS.items():
        block = raw.get(block_name)
        has_signal = signals.get(block_name, False)
        if has_signal and isinstance(block, dict) and block:
            out[block_name] = copy.deepcopy(block)
        elif allow_stubs:
            out[block_name] = copy.deepcopy(default_block)
        elif has_signal:
            out[block_name] = copy.deepcopy(block)

    # schedule_raw: use kickoff date as soft stub when missing (debug export only)
    if allow_stubs and "schedule_raw" in out:
        if not out["schedule_raw"].get("previous_match_date_home"):
            d = _kickoff_date_stub(kickoff_iso)
            if d:
                out["schedule_raw"]["previous_match_date_home"] = d
                out["schedule_raw"]["previous_match_date_away"] = d

    return out


def write_fixture_json(path: Path, record: Dict[str, Any]) -> Path:
    """Write fixture-compatible JSON (UTF-8, indented)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
