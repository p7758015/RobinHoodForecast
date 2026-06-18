"""
Batch settlement for league eval-pool runs from Flashscore finished matches.

Writes final scores into ``match_results`` (shared with offline evaluation).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from football_agent.eval_pool.scope import filter_pool_keys, resolve_pool_entry
from football_agent.storage.v2_database import V2Database

logger = logging.getLogger(__name__)

_FINISHED_STATUSES = frozenset(
    {
        "finished",
        "ft",
        "ended",
        "aet",
        "pen",
        "after pen.",
        "after penalties",
        "full-time",
        "full time",
    }
)


def is_finished_status(status: Optional[str]) -> bool:
    s = (status or "").strip().lower()
    if not s:
        return False
    if s in _FINISHED_STATUSES:
        return True
    return s.startswith("finished") or s == "ft"


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    m = re.match(r"^(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def extract_final_score(raw: dict) -> Optional[Tuple[int, int]]:
    """Best-effort score extraction from Flashscore raw list/detail payloads."""
    hs = _parse_int(raw.get("home_score"))
    aw = _parse_int(raw.get("away_score"))
    if hs is None:
        hs = _parse_int(raw.get("home_goals"))
    if aw is None:
        aw = _parse_int(raw.get("away_goals"))

    score_block = raw.get("score")
    if isinstance(score_block, dict):
        if hs is None and score_block.get("home") is not None:
            hs = _parse_int(score_block.get("home"))
        if hs is None and score_block.get("home_score") is not None:
            hs = _parse_int(score_block.get("home_score"))
        if aw is None and score_block.get("away") is not None:
            aw = _parse_int(score_block.get("away"))
        if aw is None and score_block.get("away_score") is not None:
            aw = _parse_int(score_block.get("away_score"))

    result_block = raw.get("result")
    if isinstance(result_block, dict):
        if hs is None:
            hs = _parse_int(result_block.get("home") or result_block.get("home_score"))
        if aw is None:
            aw = _parse_int(result_block.get("away") or result_block.get("away_score"))

    if hs is None or aw is None:
        return None
    return hs, aw


def _parse_date_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except ValueError:
        return None


_FLASHSCORE_TIME_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.")


def date_from_flashscore_display_time(
    time_val: Any,
    *,
    reference_year: int,
) -> Optional[str]:
    """
    Parse Flashscore fixture list ``time`` values like ``20.06. 14:30``.

    The scraper often leaves ``date``/``kickoff_utc`` null while embedding
    day/month in ``time``; year comes from the wave/discovery query context.
    """
    if time_val is None:
        return None
    m = _FLASHSCORE_TIME_DATE_RE.match(str(time_val).strip())
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{reference_year}-{month:02d}-{day:02d}"


def _reference_year_from_raw(raw: dict, *, reference_year: Optional[int] = None) -> Optional[int]:
    if reference_year is not None:
        return reference_year
    explicit = raw.get("_discovery_reference_year")
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    dfrom = raw.get("_discovery_date_from")
    if dfrom and len(str(dfrom)) >= 4:
        try:
            return int(str(dfrom)[:4])
        except ValueError:
            return None
    return None


def kickoff_date_from_raw(
    raw: dict,
    *,
    fallback_date: Optional[str] = None,
    reference_year: Optional[int] = None,
) -> Optional[str]:
    for key in ("kickoff_utc", "date", "match_date"):
        parsed = _parse_date_token(raw.get(key))
        if parsed:
            return parsed
    year = _reference_year_from_raw(raw, reference_year=reference_year)
    if year is not None:
        parsed = date_from_flashscore_display_time(raw.get("time"), reference_year=year)
        if parsed:
            return parsed
    return fallback_date


def settle_league_pool_from_flashscore(
    *,
    date_from: str,
    date_to: str,
    league_keys: Optional[Sequence[str]] = None,
    db_path: str | Path | None = None,
    scraper_url: Optional[str] = None,
    fetch_matches_for_date: Optional[Callable[[str], List[dict]]] = None,
) -> Dict[str, Any]:
    """
    For each date, load Flashscore matches, filter wave-1 pool + finished status,
    persist scores into ``match_results``.
    """
    pool_entries = filter_pool_keys(league_keys)
    keys = tuple(e.key for e in pool_entries)
    summary: Dict[str, Any] = {
        "pipeline": "league_eval_pool_settle",
        "date_from": date_from,
        "date_to": date_to,
        "league_keys": list(keys),
        "fixtures_scanned": 0,
        "fixtures_in_scope": 0,
        "finished_in_scope": 0,
        "results_saved": 0,
        "skipped_no_score": 0,
        "skipped_not_finished": 0,
        "out_of_scope_skipped": 0,
        "saved": [],
        "errors": [],
    }

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date_to must be >= date_from")

    if fetch_matches_for_date is None:
        from football_agent.collectors.flashscore.client import FlashscoreCollectorClient
        from football_agent import config

        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        if not base:
            raise ValueError("FLASHSCORE_SCRAPER_URL not configured")
        client = FlashscoreCollectorClient(
            base,
            api_key=config.FLASHSCORE_SCRAPER_API_KEY,
            timeout_s=config.FLASHSCORE_SCRAPER_TIMEOUT_S,
        )
        fetch_matches_for_date = client.fetch_matches_for_date_raw

    db = V2Database(db_path)
    try:
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                raw_list = fetch_matches_for_date(date_str)
            except Exception as exc:
                logger.exception("settle fetch failed date=%s", date_str)
                summary["errors"].append({"date": date_str, "error": str(exc)})
                current += timedelta(days=1)
                continue

            summary["fixtures_scanned"] += len(raw_list)
            for raw in raw_list:
                comp_name = str(
                    raw.get("competition_name") or raw.get("competition") or raw.get("league_name") or ""
                )
                comp_country = raw.get("competition_country")
                pool_entry = resolve_pool_entry(comp_name, str(comp_country) if comp_country else None)
                if pool_entry is None or pool_entry.key not in keys:
                    summary["out_of_scope_skipped"] += 1
                    continue

                summary["fixtures_in_scope"] += 1
                status = str(raw.get("status") or "")
                if not is_finished_status(status):
                    summary["skipped_not_finished"] += 1
                    continue

                summary["finished_in_scope"] += 1
                score = extract_final_score(raw)
                if score is None:
                    summary["skipped_no_score"] += 1
                    continue

                home = str(raw.get("home_team_name") or raw.get("home") or "").strip()
                away = str(raw.get("away_team_name") or raw.get("away") or "").strip()
                match_date = kickoff_date_from_raw(raw, fallback_date=date_str)
                if not home or not away or not match_date:
                    summary["skipped_no_score"] += 1
                    continue

                hs, aw = score
                db.save_match_result(match_date, home, away, hs, aw)
                summary["results_saved"] += 1
                summary["saved"].append(
                    {
                        "match_date": match_date,
                        "pool_key": pool_entry.key,
                        "home": home,
                        "away": away,
                        "home_score": hs,
                        "away_score": aw,
                    }
                )

            current += timedelta(days=1)
    finally:
        db.close()

    logger.info("league eval pool settlement done: %s", summary)
    return summary
