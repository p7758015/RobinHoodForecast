"""
Run OpenClaw bridge locally.

  python -m football_agent.openclaw_bridge --port 8787 --mode prototype
  python -m football_agent.openclaw_bridge --port 8787 --mode live_assisted --gateway http://localhost:18789
"""

from __future__ import annotations

import argparse
import logging
import os

from football_agent import config
from football_agent.openclaw_bridge.enricher import BridgeEnricher
from football_agent.openclaw_bridge.models import BridgeMode
from football_agent.openclaw_bridge.server import serve_forever


def _build_enricher_factory(args: argparse.Namespace):
    mode = BridgeMode(args.mode)
    gateway = args.gateway or os.getenv("OPENCLAW_GATEWAY_URL") or os.getenv("OPENCLAW_BASE_URL")

    def factory() -> BridgeEnricher:
        return BridgeEnricher(
            mode=mode,
            openclaw_gateway_url=gateway,
            gateway_timeout_s=float(args.gateway_timeout),
            api_key=args.api_key or config.OPENCLAW_BRIDGE_API_KEY,
            model=args.model or config.OPENCLAW_BRIDGE_MODEL,
            chat_path=args.chat_path or config.OPENCLAW_BRIDGE_CHAT_PATH,
            live_timeout_s=float(args.live_timeout or config.OPENCLAW_BRIDGE_LIVE_TIMEOUT_S),
        )

    return factory


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw bridge HTTP service for football_agent enrichment")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("OPENCLAW_BRIDGE_PORT", "8787")))
    parser.add_argument(
        "--mode",
        choices=[m.value for m in BridgeMode],
        default=os.getenv("OPENCLAW_BRIDGE_MODE", BridgeMode.PROTOTYPE.value),
    )
    parser.add_argument(
        "--gateway",
        help="Upstream OpenClaw gateway URL for live_assisted probe (default OPENCLAW_GATEWAY_URL / OPENCLAW_BASE_URL).",
    )
    parser.add_argument("--gateway-timeout", type=float, default=8.0)
    parser.add_argument("--api-key", help="OpenClaw gateway API key (default OPENCLAW_BRIDGE_API_KEY).")
    parser.add_argument("--model", help="Chat model (default OPENCLAW_BRIDGE_MODEL).")
    parser.add_argument("--chat-path", help="Chat endpoint path (default /v1/chat/completions).")
    parser.add_argument("--live-timeout", type=float, help="Live backend timeout seconds.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve_forever(args.host, args.port, enricher_factory=_build_enricher_factory(args))


if __name__ == "__main__":
    main()
