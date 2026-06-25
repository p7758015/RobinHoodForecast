"""Competition resolver: registry → aliases → scraper search (+ optional Brave normalize)."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, List, Optional

from football_agent import config
from football_agent.discovery.aliases import lookup_static_alias
from football_agent.discovery.brave_normalize import normalize_competition_query
from football_agent.discovery.models import (
    CompetitionCandidate,
    CompetitionResolveResult,
    ResolvedCompetition,
)
from football_agent.discovery.registry_lookup import lookup_registry_by_pool_entry, lookup_registry_candidates
from football_agent.discovery.scraper_client import FlashscoreDiscoveryClient
from football_agent.domain.competition_family import CompetitionFamily, classify_competition_family
from football_agent.flashscore.models import FlashscoreMeta
from football_agent.services.competition_classifier import classify_competition_meta

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _candidate_from_scraper_row(row: dict) -> CompetitionCandidate:
    name = str(row.get("competition_name") or row.get("name") or "Unknown")
    country = row.get("competition_country") or row.get("country")
    url = str(row.get("url") or row.get("competition_url") or "")
    fixtures_url = row.get("fixtures_url") or (f"{url.rstrip('/')}/fixtures/" if url else None)
    meta = FlashscoreMeta(
        match_id="discovery",
        source_url=url,
        competition_name=name,
        competition_country=str(country) if country else None,
        home_team_name="Home",
        away_team_name="Away",
    )
    clf = classify_competition_meta(meta)
    confidence = "high" if clf.is_league_eligible else "medium"
    return CompetitionCandidate(
        competition_name=name,
        country=str(country) if country else None,
        url=url,
        fixtures_url=str(fixtures_url) if fixtures_url else None,
        source="scraper_search",
        confidence=confidence,
        league_slug=row.get("league_slug"),
        country_slug=row.get("country_slug"),
    )


def _dedupe_candidates(candidates: List[CompetitionCandidate]) -> List[CompetitionCandidate]:
    by_name: dict[str, CompetitionCandidate] = {}
    by_url: dict[str, CompetitionCandidate] = {}
    for c in candidates:
        name_key = _norm(c.competition_name)
        url_key = _norm(c.url) if c.url else ""
        prev = by_name.get(name_key)
        if prev is None or (not prev.url and c.url):
            by_name[name_key] = c
        if url_key:
            prev_u = by_url.get(url_key)
            if prev_u is None:
                by_url[url_key] = c
    # Prefer name-indexed set; add url-only extras
    merged = {id(c): c for c in by_name.values()}
    for c in by_url.values():
        merged[id(c)] = c
    return list(merged.values())


def _pick_best(
    candidates: List[CompetitionCandidate],
    query: str,
    *,
    preferred_name: Optional[str] = None,
) -> Optional[CompetitionCandidate]:
    if not candidates:
        return None
    q = _norm(query)
    pref = _norm(preferred_name) if preferred_name else ""

    def _score(c: CompetitionCandidate) -> tuple[int, int]:
        name = _norm(c.competition_name)
        s = 0
        if pref and (pref in name or name in pref):
            s += 10
        if c.source.startswith("alias"):
            s += 5
        if c.source == "registry":
            s += 4
        if c.confidence == "high":
            s += 2
        if name == q:
            s += 8
        fam = classify_competition_family(
            competition_code=c.registry_code,
            competition_name=c.competition_name,
            country=c.country,
        ).family
        q_women = any(tok in q for tok in ("women", "woman", "femin", "frauen", "ladies"))
        if q_women:
            if fam == CompetitionFamily.WOMEN_SENIOR_LEAGUE:
                s += 8
            elif fam == CompetitionFamily.MEN_SENIOR_LEAGUE:
                s -= 6
        else:
            if fam == CompetitionFamily.MEN_SENIOR_LEAGUE:
                s += 4
            elif fam == CompetitionFamily.WOMEN_SENIOR_LEAGUE:
                s -= 6
        return (s, -len(candidates))

    ranked = sorted(candidates, key=_score, reverse=True)
    top = ranked[0]
    if len(ranked) > 1 and ranked[1] and _score(top) == _score(ranked[1]):
        return None
    return top


class CompetitionResolverService:
    """
    resolve_competition(query_text) -> CompetitionResolveResult

    Tier order:
    1. Registry / wave-1 pool fast-path
    2. Static aliases (+ classifier hint on canonical name)
    3. Flashscore scraper search (truth for unknown leagues)
    4. Optional Brave query normalization before scraper retry
    """

    def __init__(
        self,
        *,
        scraper_url: Optional[str] = None,
        scraper_api_key: Optional[str] = None,
        discovery_client: Optional[FlashscoreDiscoveryClient] = None,
        search_fn: Optional[Callable[[str, int], List[dict]]] = None,
        enable_brave_normalize: Optional[bool] = None,
    ) -> None:
        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        self._client = discovery_client or (
            FlashscoreDiscoveryClient(base, api_key=scraper_api_key or config.FLASHSCORE_SCRAPER_API_KEY)
            if base
            else None
        )
        self._search_fn = search_fn
        self._enable_brave = (
            enable_brave_normalize
            if enable_brave_normalize is not None
            else bool(config.DISCOVERY_BRAVE_NORMALIZE)
        )

    def resolve_competition_for_pool_entry(
        self,
        entry: "LeaguePoolEntry",
    ) -> CompetitionResolveResult:
        """Resolve eval-pool entry via registry Flashscore URL (no free-text ambiguity)."""
        from football_agent.eval_pool.scope import LeaguePoolEntry as _Entry

        if not isinstance(entry, _Entry):
            raise TypeError("entry must be LeaguePoolEntry")
        cand = lookup_registry_by_pool_entry(entry)
        if cand is None or not cand.url:
            return CompetitionResolveResult(
                query=entry.key,
                warnings=["pool_entry_not_mapped"],
            )
        return CompetitionResolveResult(
            query=entry.key,
            candidates=[cand],
            resolved=ResolvedCompetition(candidate=cand, normalized_query=entry.key),
            ambiguous=False,
            normalized_query=entry.key,
            sources_tried=["pool_registry"],
        )

    def resolve_competition(
        self,
        query_text: str,
        *,
        limit: int = 8,
        allow_ambiguous: bool = False,
    ) -> CompetitionResolveResult:
        query = (query_text or "").strip()
        sources_tried: List[str] = []
        warnings: List[str] = []
        candidates: List[CompetitionCandidate] = []

        if not query:
            return CompetitionResolveResult(
                query=query,
                warnings=["empty_query"],
            )

        # 1) Registry fast-path
        sources_tried.append("registry")
        reg = lookup_registry_candidates(query)
        candidates.extend(reg)

        # Prefer a single registry candidate with an explicit Flashscore URL.
        reg_with_url = [c for c in reg if c.url]
        if len(reg_with_url) == 1:
            only = reg_with_url[0]
            return CompetitionResolveResult(
                query=query,
                candidates=_dedupe_candidates(candidates),
                resolved=ResolvedCompetition(candidate=only, normalized_query=query),
                ambiguous=False,
                normalized_query=query,
                sources_tried=sources_tried,
            )

        # 2) Static alias → synthetic candidate (scraper will confirm URL)
        alias = lookup_static_alias(query)
        if alias:
            sources_tried.append("alias")
            name, country = alias
            meta = FlashscoreMeta(
                match_id="alias",
                source_url="",
                competition_name=name,
                competition_country=country,
                home_team_name="Home",
                away_team_name="Away",
            )
            clf = classify_competition_meta(meta)
            if not any(_norm(c.competition_name) == _norm(name) for c in candidates):
                candidates.append(
                    CompetitionCandidate(
                        competition_name=name,
                        country=country,
                        url="",
                        source="alias",
                        confidence="high" if clf.is_league_eligible else "medium",
                    )
                )

        reg_high = [c for c in reg if c.confidence == "high" and c.url]
        if len(reg_high) == 1 and not alias:
            only = reg_high[0]
            return CompetitionResolveResult(
                query=query,
                candidates=_dedupe_candidates(candidates),
                resolved=ResolvedCompetition(candidate=only, normalized_query=query),
                ambiguous=False,
                normalized_query=query,
                sources_tried=sources_tried,
            )

        # 3) Scraper search
        search_queries = [query]
        normalized: Optional[str] = None
        if alias:
            name, country = alias
            normalized = f"{name} {country}" if country else name
            search_queries.insert(0, normalized)
        elif self._enable_brave:
            sources_tried.append("brave_normalize")
            brave_q = normalize_competition_query(query)
            if brave_q and brave_q not in search_queries:
                normalized = brave_q
                search_queries.insert(0, brave_q)

        sources_tried.append("scraper_search")
        scraper_rows: List[dict] = []
        for sq in search_queries:
            rows = self._search_scraper(sq, limit)
            if rows:
                scraper_rows = rows
                break
        if not scraper_rows:
            warnings.append("scraper_search_empty")

        for row in scraper_rows:
            cand = _candidate_from_scraper_row(row)
            if alias and normalized:
                cand = replace(cand, source="alias+scraper_search")
            candidates.append(cand)

        candidates = _dedupe_candidates(candidates)
        preferred_name = alias[0] if alias else (normalized or None)
        best = _pick_best(candidates, query, preferred_name=preferred_name)
        ambiguous = len(candidates) > 1 and best is None

        if ambiguous and not allow_ambiguous:
            return CompetitionResolveResult(
                query=query,
                candidates=candidates,
                resolved=None,
                ambiguous=True,
                normalized_query=normalized or query,
                sources_tried=sources_tried,
                warnings=warnings + ["ambiguous_competition_query"],
            )

        if best is None and candidates:
            best = candidates[0]

        resolved = (
            ResolvedCompetition(
                candidate=best,
                ambiguous=ambiguous,
                normalized_query=normalized or query,
            )
            if best
            else None
        )

        return CompetitionResolveResult(
            query=query,
            candidates=candidates,
            resolved=resolved,
            ambiguous=ambiguous and resolved is None,
            normalized_query=normalized or query,
            sources_tried=sources_tried,
            warnings=warnings,
        )

    def _search_scraper(self, query: str, limit: int) -> List[dict]:
        if self._search_fn is not None:
            return self._search_fn(query, limit)
        if self._client is None:
            logger.warning("Flashscore discovery client not configured")
            return []
        try:
            return self._client.search_competitions(query, limit=limit)
        except Exception as exc:
            logger.warning("scraper competition search failed: %s", exc)
            return []
