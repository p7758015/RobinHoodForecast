"""Discovery layer contracts (competition resolve + fixture list)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CompetitionCandidate:
    """One resolved or discovered competition option."""

    competition_name: str
    country: Optional[str]
    url: str
    fixtures_url: Optional[str] = None
    sport: str = "football"
    source: str = "unknown"  # registry | alias | classifier_hint | scraper_search | brave_normalized
    confidence: str = "medium"  # high | medium | low
    registry_code: Optional[str] = None
    league_slug: Optional[str] = None
    country_slug: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "competition_name": self.competition_name,
            "country": self.country,
            "url": self.url,
            "fixtures_url": self.fixtures_url,
            "sport": self.sport,
            "source": self.source,
            "confidence": self.confidence,
            "registry_code": self.registry_code,
            "league_slug": self.league_slug,
            "country_slug": self.country_slug,
        }


@dataclass(frozen=True)
class ResolvedCompetition:
    """Canonical competition chosen for fixture discovery."""

    candidate: CompetitionCandidate
    ambiguous: bool = False
    normalized_query: Optional[str] = None

    @property
    def competition_name(self) -> str:
        return self.candidate.competition_name

    @property
    def url(self) -> str:
        return self.candidate.url

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "ambiguous": self.ambiguous,
            "normalized_query": self.normalized_query,
        }


@dataclass(frozen=True)
class CompetitionResolveResult:
    query: str
    candidates: List[CompetitionCandidate] = field(default_factory=list)
    resolved: Optional[ResolvedCompetition] = None
    ambiguous: bool = False
    normalized_query: Optional[str] = None
    sources_tried: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "candidates": [c.to_dict() for c in self.candidates],
            "resolved": self.resolved.to_dict() if self.resolved else None,
            "ambiguous": self.ambiguous,
            "normalized_query": self.normalized_query,
            "sources_tried": list(self.sources_tried),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DiscoveredFixture:
    match_id: str
    match_url: str
    home_team: str
    away_team: str
    kickoff_utc: Optional[str] = None
    match_date: Optional[str] = None
    status: str = "scheduled"
    competition_name: Optional[str] = None
    competition_country: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "match_url": self.match_url,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "kickoff_utc": self.kickoff_utc,
            "match_date": self.match_date,
            "status": self.status,
            "competition_name": self.competition_name,
            "competition_country": self.competition_country,
        }


@dataclass(frozen=True)
class FixtureDiscoveryResult:
    competition: ResolvedCompetition
    date_from: str
    date_to: str
    fixtures: List[DiscoveredFixture] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.fixtures)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "competition": self.competition.to_dict(),
            "date_from": self.date_from,
            "date_to": self.date_to,
            "count": self.count,
            "fixtures": [f.to_dict() for f in self.fixtures],
            "warnings": list(self.warnings),
        }
