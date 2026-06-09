"""
OpenClaw ingestion layer: raw collector payload → MatchAnalysisSnapshotV2.

Legacy Football-Data / API-Football path is untouched; use this stack in parallel
(OpenClawLeagueAnalysisService) when wired to your OpenClaw deployment.
"""

from football_agent.openclaw.adapter import OpenClawSnapshotBuilder
from football_agent.openclaw.client import OpenClawClient, OpenClawClientError, OpenClawConfigurationError
from football_agent.openclaw.models import OpenClawMatchPayload
from football_agent.openclaw.service import OpenClawLeagueAnalysisService

__all__ = [
    "OpenClawClient",
    "OpenClawClientError",
    "OpenClawConfigurationError",
    "OpenClawMatchPayload",
    "OpenClawSnapshotBuilder",
    "OpenClawLeagueAnalysisService",
]
