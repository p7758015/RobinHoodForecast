"""OpenClaw bridge — stable JSON enrichment API for football_agent."""

from football_agent.openclaw_bridge.enricher import BridgeEnricher, BridgeMatchInput
from football_agent.openclaw_bridge.models import BridgeMode

__all__ = ["BridgeEnricher", "BridgeMatchInput", "BridgeMode"]
