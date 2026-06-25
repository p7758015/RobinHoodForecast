"""League eval-pool preflight: registry / pool / scraper readiness for a date window."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_agent import config
from football_agent.eval_pool.fixture_sources import fetch_fixtures_for_pool_entry
from football_agent.eval_pool.scope import (
    WAVE1_POOL_KEYS,
    LeaguePoolEntry,
    all_pool_entries,
    filter_pool_keys,
)
from football_agent.league_registry import get_league_config

logger = logging.getLogger(__name__)

# User-facing expected league token → eval-pool key.
EXPECTED_LEAGUE_ALIASES: Dict[str, str] = {
    "latvia": "latvia_virsliga",
    "virsliga": "latvia_virsliga",
    "morocco": "morocco_botola",
    "botola": "morocco_botola",
    "belarus": "belarus_premier",
    "brazil": "brazil_serie_b",
    "brazil_serie_b": "brazil_serie_b",
    "serie_b": "brazil_serie_b",
    "estonia": "estonia_meistriliiga",
    "meistriliiga": "estonia_meistriliiga",
    "estonia_premium": "estonia_premium_liiga",
    "premium_liiga": "estonia_premium_liiga",
    "ireland": "ireland_premier",
    "china": "china_super_league",
    "csl": "china_super_league",
    "finland": "finland_veikkausliiga",
    "veikkausliiga": "finland_veikkausliiga",
    "kazakhstan": "kazakhstan_premier",
    "lithuania": "lithuania_a_lyga",
    "a_lyga": "lithuania_a_lyga",
    "chile": "chile_primera",
}


class PreflightStatus(str, Enum):
    IN_POOL_AND_SUPPORTED = "IN_POOL_AND_SUPPORTED"
    SUPPORTED_BUT_OUT_OF_POOL = "SUPPORTED_BUT_OUT_OF_POOL"
    IN_REGISTRY_BUT_NO_SCRAPER_MAPPING = "IN_REGISTRY_BUT_NO_SCRAPER_MAPPING"
    UNKNOWN_LEAGUE = "UNKNOWN_LEAGUE"
    NO_FIXTURES_FOUND = "NO_FIXTURES_FOUND"
    NOT_IN_DEFAULT_ACCUMULATE_POOL = "NOT_IN_DEFAULT_ACCUMULATE_POOL"


@dataclass
class LeaguePreflightRow:
    expected_token: str
    pool_key: Optional[str]
    status: PreflightStatus
    registry_code: Optional[str] = None
    display_name: Optional[str] = None
    flashscore_url: Optional[str] = None
    fixtures_found: Optional[int] = None
    in_default_accumulate_pool: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "expected_token": self.expected_token,
            "pool_key": self.pool_key,
            "status": self.status.value,
            "registry_code": self.registry_code,
            "display_name": self.display_name,
            "flashscore_url": self.flashscore_url,
            "fixtures_found": self.fixtures_found,
            "in_default_accumulate_pool": self.in_default_accumulate_pool,
            "notes": list(self.notes),
        }


@dataclass
class PreflightReport:
    date_from: str
    date_to: str
    scraper_configured: bool
    default_accumulate_pool_keys: Tuple[str, ...]
    full_pool_keys: Tuple[str, ...]
    rows: List[LeaguePreflightRow] = field(default_factory=list)
    discovered_competitions: List[dict] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        ready: List[str] = []
        missing_registry: List[str] = []
        missing_pool: List[str] = []
        missing_scraper: List[str] = []
        no_fixtures: List[str] = []
        unknown: List[str] = []
        default_only_gap: List[str] = []

        for row in self.rows:
            tok = row.expected_token
            if row.status == PreflightStatus.IN_POOL_AND_SUPPORTED:
                ready.append(tok)
                if not row.in_default_accumulate_pool:
                    default_only_gap.append(tok)
            elif row.status == PreflightStatus.SUPPORTED_BUT_OUT_OF_POOL:
                missing_pool.append(tok)
            elif row.status == PreflightStatus.IN_REGISTRY_BUT_NO_SCRAPER_MAPPING:
                missing_scraper.append(tok)
            elif row.status == PreflightStatus.NO_FIXTURES_FOUND:
                no_fixtures.append(tok)
            elif row.status == PreflightStatus.UNKNOWN_LEAGUE:
                unknown.append(tok)
            elif row.status == PreflightStatus.NOT_IN_DEFAULT_ACCUMULATE_POOL:
                default_only_gap.append(tok)

        return {
            "ready_now": ready,
            "requires_registry_add": missing_registry,
            "requires_pool_inclusion": missing_pool,
            "requires_scraper_mapping": missing_scraper,
            "no_fixtures_in_window": no_fixtures,
            "unknown_leagues": unknown,
            "in_pool_but_not_default_accumulate": default_only_gap,
        }

    def to_dict(self) -> dict:
        return {
            "date_window": f"{self.date_from}..{self.date_to}",
            "scraper_configured": self.scraper_configured,
            "default_accumulate_pool_keys": list(self.default_accumulate_pool_keys),
            "full_pool_keys": list(self.full_pool_keys),
            "expected_leagues": [r.to_dict() for r in self.rows],
            "summary": self.summary,
            "discovered_competitions": self.discovered_competitions,
        }


def normalize_expected_token(token: str) -> str:
    return (token or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_expected_pool_key(token: str) -> Optional[str]:
    key = normalize_expected_token(token)
    return EXPECTED_LEAGUE_ALIASES.get(key)


def _pool_entry_by_key(key: str) -> Optional[LeaguePoolEntry]:
    for entry in all_pool_entries():
        if entry.key == key:
            return entry
    return None


def _date_range(date_from: str, date_to: str) -> List[str]:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    out: List[str] = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _count_fixtures_for_entry(
    entry: LeaguePoolEntry,
    *,
    date_from: str,
    date_to: str,
    scraper_configured: bool,
) -> Tuple[int, List[str]]:
    if not scraper_configured:
        return 0, ["scraper_not_configured"]
    warnings: List[str] = []
    total = 0
    seen_ids: set[str] = set()
    for day in _date_range(date_from, date_to):
        try:
            result = fetch_fixtures_for_pool_entry(
                entry,
                day,
                [],
                use_discovery_fallback=True,
                wave_date_from=date_from,
                wave_date_to=date_to,
            )
        except Exception as exc:
            warnings.append(f"fixture_probe_error:{entry.key}:{day}:{exc}")
            continue
        warnings.extend(result.warnings)
        for raw in result.fixtures:
            mid = str(raw.get("match_id") or raw.get("id") or "")
            dedupe = mid or f"{raw.get('home_team_name')}:{raw.get('away_team_name')}:{day}"
            if dedupe in seen_ids:
                continue
            seen_ids.add(dedupe)
            total += 1
    return total, warnings


def assess_pool_entry(
    entry: LeaguePoolEntry,
    *,
    probe_fixtures: bool,
    date_from: str,
    date_to: str,
    scraper_configured: bool,
) -> Tuple[PreflightStatus, int, List[str]]:
    notes: List[str] = []
    cfg = get_league_config(entry.registry_code)
    if cfg is None:
        return PreflightStatus.UNKNOWN_LEAGUE, 0, ["registry_missing"]
    if not cfg.flashscore_competition_url:
        return PreflightStatus.IN_REGISTRY_BUT_NO_SCRAPER_MAPPING, 0, ["flashscore_url_missing"]

    fixtures = 0
    if probe_fixtures and scraper_configured:
        fixtures, probe_notes = _count_fixtures_for_entry(
            entry,
            date_from=date_from,
            date_to=date_to,
            scraper_configured=scraper_configured,
        )
        notes.extend(probe_notes[:5])
        if fixtures == 0:
            return PreflightStatus.NO_FIXTURES_FOUND, 0, notes

    return PreflightStatus.IN_POOL_AND_SUPPORTED, fixtures, notes


def run_preflight(
    *,
    date_from: str,
    date_to: str,
    expected_leagues: Optional[Sequence[str]] = None,
    probe_fixtures: bool = True,
    scraper_url: Optional[str] = None,
) -> PreflightReport:
    scraper = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip()
    scraper_configured = bool(scraper)
    default_keys = tuple(WAVE1_POOL_KEYS)
    full_keys = tuple(e.key for e in all_pool_entries())

    tokens = list(expected_leagues or [])
    report = PreflightReport(
        date_from=date_from,
        date_to=date_to,
        scraper_configured=scraper_configured,
        default_accumulate_pool_keys=default_keys,
        full_pool_keys=full_keys,
    )

    pool_keys_seen: set[str] = set()
    for raw_token in tokens:
        token = normalize_expected_token(raw_token)
        pool_key = resolve_expected_pool_key(token)
        row = LeaguePreflightRow(expected_token=token, pool_key=pool_key, status=PreflightStatus.UNKNOWN_LEAGUE)

        if pool_key is None:
            row.notes.append("no_alias_mapping")
            report.rows.append(row)
            continue

        pool_keys_seen.add(pool_key)
        entry = _pool_entry_by_key(pool_key)
        row.in_default_accumulate_pool = pool_key in default_keys

        if entry is None:
            cfg = get_league_config(pool_key.upper())  # fallback unlikely
            if cfg:
                row.registry_code = cfg.competition_code
                row.display_name = cfg.display_name
                row.flashscore_url = cfg.flashscore_competition_url
                row.status = PreflightStatus.SUPPORTED_BUT_OUT_OF_POOL
                row.notes.append("registry_exists_pool_entry_missing")
            else:
                row.status = PreflightStatus.UNKNOWN_LEAGUE
            report.rows.append(row)
            continue

        row.registry_code = entry.registry_code
        cfg = get_league_config(entry.registry_code)
        if cfg:
            row.display_name = cfg.display_name
            row.flashscore_url = cfg.flashscore_competition_url

        status, fixtures, notes = assess_pool_entry(
            entry,
            probe_fixtures=probe_fixtures,
            date_from=date_from,
            date_to=date_to,
            scraper_configured=scraper_configured,
        )
        row.status = status
        row.fixtures_found = fixtures if probe_fixtures and scraper_configured else None
        row.notes.extend(notes)

        if status == PreflightStatus.IN_POOL_AND_SUPPORTED and not row.in_default_accumulate_pool:
            row.notes.append("use --leagues flag on accumulate; not in default wave-1 pool")

        report.rows.append(row)

    return report


def format_preflight_text(report: PreflightReport) -> str:
    lines: List[str] = [
        f"date_window: {report.date_from}..{report.date_to}",
        f"scraper_configured: {report.scraper_configured}",
        f"default_accumulate_pool: {', '.join(report.default_accumulate_pool_keys)}",
        "",
        "expected leagues:",
    ]
    for row in report.rows:
        fx = row.fixtures_found if row.fixtures_found is not None else "?"
        lines.append(
            f"- {row.expected_token} → {row.status.value} → pool_key={row.pool_key or '—'} "
            f"→ registry={row.registry_code or '—'} → fixtures_found={fx}"
        )
        if row.notes:
            lines.append(f"    notes: {', '.join(row.notes[:3])}")

    summ = report.summary
    lines.extend(
        [
            "",
            "summary:",
            f"- ready_now: {', '.join(summ['ready_now']) or '(none)'}",
            f"- requires_pool_inclusion: {', '.join(summ['requires_pool_inclusion']) or '(none)'}",
            f"- requires_scraper_mapping: {', '.join(summ['requires_scraper_mapping']) or '(none)'}",
            f"- no_fixtures_in_window: {', '.join(summ['no_fixtures_in_window']) or '(none)'}",
            f"- unknown_leagues: {', '.join(summ['unknown_leagues']) or '(none)'}",
            f"- in_pool_but_not_default_accumulate: {', '.join(summ['in_pool_but_not_default_accumulate']) or '(none)'}",
            "",
            "accumulate hint:",
            f"  python -m football_agent.debug.league_eval_pool accumulate --date-from {report.date_from} --date-to {report.date_to} "
            f"--leagues {','.join(report.full_pool_keys)}",
        ]
    )
    return "\n".join(lines)
