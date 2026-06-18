"""Authoritative fixture kickoff date extraction and wave range filtering."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from football_agent.eval_pool.settle import kickoff_date_from_raw


def reference_year_for_wave(date_from: str) -> int:
    """Season year anchor for Flashscore display-time parsing (wave preset date_from)."""
    return int(date_from[:4])


def is_discovery_fixture(raw: dict) -> bool:
    """True when fixture came from competition discovery (full calendar scrape)."""
    return bool(raw.get("_discovery_source"))


def extract_fixture_date(
    raw: dict,
    *,
    fallback_date: Optional[str] = None,
    reference_year: Optional[int] = None,
) -> Optional[str]:
    """Resolve YYYY-MM-DD for a fixture raw dict."""
    year = reference_year
    if year is None and raw.get("_discovery_date_from"):
        year = reference_year_for_wave(str(raw["_discovery_date_from"]))
    elif year is None and not is_discovery_fixture(raw):
        pass
    return kickoff_date_from_raw(raw, fallback_date=fallback_date, reference_year=year)


def _loop_date_fallback(raw: dict, loop_date: Optional[str], *, allow_loop_date_fallback: bool) -> Optional[str]:
    """Loop-day fallback is only for list-by-date fixtures, never discovery."""
    if not allow_loop_date_fallback or not loop_date or is_discovery_fixture(raw):
        return None
    return loop_date


def fixture_in_date_range(
    raw: dict,
    date_from: str,
    date_to: str,
    *,
    loop_date: Optional[str] = None,
    allow_loop_date_fallback: bool = True,
) -> bool:
    """
    True when fixture kickoff date is within [date_from, date_to] inclusive.

    Discovery fixtures require an explicit parseable kickoff date in range.
    List-by-date fixtures may use loop-day fallback when kickoff is missing.
    """
    ref_year = reference_year_for_wave(date_from)
    fd = extract_fixture_date(raw, fallback_date=None, reference_year=ref_year)
    if fd is None:
        fb = _loop_date_fallback(raw, loop_date, allow_loop_date_fallback=allow_loop_date_fallback)
        if fb and date_from <= fb <= date_to:
            return True
        return False
    return date_from <= fd <= date_to


def evaluate_fixture_date_guard(
    raw: dict,
    date_from: str,
    date_to: str,
    *,
    loop_date: Optional[str] = None,
    allow_loop_date_fallback: bool = True,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Return (explicit_date, fixture_date_for_processing, in_range).

    ``fixture_date_for_processing`` is explicit kickoff when present, else loop-day
    fallback for list-by-date only.
    """
    ref_year = reference_year_for_wave(date_from)
    explicit = extract_fixture_date(raw, fallback_date=None, reference_year=ref_year)
    fallback = _loop_date_fallback(raw, loop_date, allow_loop_date_fallback=allow_loop_date_fallback)
    fixture_date = explicit or fallback
    if fixture_date is None:
        return explicit, None, False
    in_range = date_from <= fixture_date <= date_to
    return explicit, fixture_date, in_range


def filter_fixtures_in_date_range(
    fixtures: Sequence[dict],
    date_from: str,
    date_to: str,
    *,
    loop_date: Optional[str] = None,
    allow_loop_date_fallback: bool = True,
) -> Tuple[List[dict], int]:
    """Return (in_range, out_of_range_skipped_count)."""
    kept: List[dict] = []
    skipped = 0
    for raw in fixtures:
        if fixture_in_date_range(
            raw,
            date_from,
            date_to,
            loop_date=loop_date,
            allow_loop_date_fallback=allow_loop_date_fallback,
        ):
            kept.append(raw)
        else:
            skipped += 1
    return kept, skipped
