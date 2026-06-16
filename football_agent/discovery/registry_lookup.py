"""Registry fast-path lookup for competition queries."""

from __future__ import annotations

from typing import List, Optional

from football_agent.discovery.models import CompetitionCandidate
from football_agent.eval_pool.scope import WAVE1_LEAGUE_POOL, resolve_pool_entry
from football_agent.league_registry import get_league_config, list_registry_codes


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _slug_path(country: Optional[str], name: str) -> str:
    """Best-effort Flashscore path when registry has no stored URL."""
    country_slug = (country or "world").strip().lower().replace(" ", "-")
    league_slug = name.strip().lower().replace(" ", "-")
    return f"https://www.flashscore.com/football/{country_slug}/{league_slug}/"


def lookup_registry_candidates(query: str) -> List[CompetitionCandidate]:
    """Match query against registry display names and wave-1 pool entries."""
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
        if q == dn or q == code_l or dn in q or q in dn:
            country = cfg.country
            url = _slug_path(country, cfg.display_name)
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
        for entry in WAVE1_LEAGUE_POOL:
            if _norm(entry.display_name) in q or q in _norm(entry.display_name):
                pool = entry
                break
            if any(p in q for p in entry.name_patterns):
                pool = entry
                break

    if pool is not None and not any(c.registry_code == pool.registry_code for c in found):
        country = pool.countries[0].title() if pool.countries else None
        name = pool.display_name
        url = _slug_path(country, name.split()[-1] if " " in name else name)
        found.append(
            CompetitionCandidate(
                competition_name=name,
                country=country,
                url=url,
                fixtures_url=f"{url.rstrip('/')}/fixtures/",
                source="registry",
                confidence="high",
                registry_code=pool.registry_code,
            )
        )

    return found


def lookup_registry_by_code(competition_code: str) -> Optional[CompetitionCandidate]:
    cfg = get_league_config(competition_code)
    if cfg is None:
        return None
    url = _slug_path(cfg.country, cfg.display_name)
    return CompetitionCandidate(
        competition_name=cfg.display_name,
        country=cfg.country,
        url=url,
        fixtures_url=f"{url.rstrip('/')}/fixtures/",
        source="registry",
        confidence="high",
        registry_code=cfg.competition_code,
    )
