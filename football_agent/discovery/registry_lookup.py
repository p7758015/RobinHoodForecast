"""Registry fast-path lookup for competition queries."""

from __future__ import annotations

from typing import List, Optional

from football_agent.discovery.models import CompetitionCandidate
from football_agent.eval_pool.scope import LeaguePoolEntry, all_pool_entries, resolve_pool_entry
from football_agent.league_registry import get_league_config, list_registry_codes


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _slug_path(country: Optional[str], name: str) -> str:
    """Best-effort Flashscore path when registry has no stored URL."""
    country_slug = (country or "world").strip().lower().replace(" ", "-")
    league_slug = name.strip().lower().replace(" ", "-")
    return f"https://www.flashscore.com/football/{country_slug}/{league_slug}/"


def _flashscore_url_for_registry_code(registry_code: str, *, country: Optional[str], display_name: str) -> str:
    cfg = get_league_config(registry_code)
    if cfg and cfg.flashscore_competition_url:
        return cfg.flashscore_competition_url
    return _slug_path(country, display_name)


def lookup_registry_by_pool_entry(entry: LeaguePoolEntry) -> Optional[CompetitionCandidate]:
    """Deterministic Flashscore mapping for eval-pool entries (no text ambiguity)."""
    cfg = get_league_config(entry.registry_code)
    country = cfg.country if cfg and cfg.country else (entry.countries[0].title() if entry.countries else None)
    display_name = cfg.display_name if cfg else entry.display_name
    url = _flashscore_url_for_registry_code(entry.registry_code, country=country, display_name=display_name)
    return CompetitionCandidate(
        competition_name=display_name,
        country=country,
        url=url,
        fixtures_url=f"{url.rstrip('/')}/fixtures/",
        source="pool_registry",
        confidence="high",
        registry_code=entry.registry_code,
    )


def lookup_registry_candidates(query: str) -> List[CompetitionCandidate]:
    """Match query against registry display names and wave pool entries."""
    q = _norm(query)
    if not q:
        return []

    found: List[CompetitionCandidate] = []

    for code in list_registry_codes():
        cfg = get_league_config(code)
        if cfg is None:
            continue
        dn = _norm(cfg.display_name)
        code_l = code.lower()
        country_tokens = {_norm(cfg.country)} if cfg.country else set()
        country_in_q = any(token and token in q for token in country_tokens)
        if q == dn or q == code_l:
            matched = True
        elif (dn in q or q in dn) and (not cfg.country or country_in_q or dn == q):
            matched = True
        else:
            matched = False
        if not matched:
            continue
        country = cfg.country
        url = _flashscore_url_for_registry_code(code, country=country, display_name=cfg.display_name)
        found.append(
            CompetitionCandidate(
                competition_name=cfg.display_name,
                country=country,
                url=url,
                fixtures_url=f"{url.rstrip('/')}/fixtures/",
                source="registry",
                confidence="high",
                registry_code=code,
            )
        )

    pool = resolve_pool_entry(query, None)
    if pool is None:
        for entry in all_pool_entries():
            if _norm(entry.display_name) in q or q in _norm(entry.display_name):
                pool = entry
                break
            if any(p in q for p in entry.name_patterns):
                pool = entry
                break

    if pool is not None and not any(c.registry_code == pool.registry_code for c in found):
        cand = lookup_registry_by_pool_entry(pool)
        if cand is not None:
            found.append(cand)

    return found


def lookup_registry_by_code(competition_code: str) -> Optional[CompetitionCandidate]:
    cfg = get_league_config(competition_code)
    if cfg is None:
        return None
    url = _flashscore_url_for_registry_code(
        cfg.competition_code,
        country=cfg.country,
        display_name=cfg.display_name,
    )
    return CompetitionCandidate(
        competition_name=cfg.display_name,
        country=cfg.country,
        url=url,
        fixtures_url=f"{url.rstrip('/')}/fixtures/",
        source="registry",
        confidence="high",
        registry_code=cfg.competition_code,
    )
