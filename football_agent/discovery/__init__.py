"""Universal competition + fixture discovery (additive layer)."""

from football_agent.discovery.competition_resolver import CompetitionResolverService
from football_agent.discovery.fixture_discovery import FixtureDiscoveryService
from football_agent.discovery.models import (
    CompetitionCandidate,
    CompetitionResolveResult,
    FixtureDiscoveryResult,
    ResolvedCompetition,
)

__all__ = [
    "CompetitionCandidate",
    "CompetitionResolveResult",
    "CompetitionResolverService",
    "FixtureDiscoveryResult",
    "FixtureDiscoveryService",
    "ResolvedCompetition",
]
