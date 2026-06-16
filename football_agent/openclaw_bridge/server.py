"""Minimal stdlib HTTP server for OpenClaw bridge (no extra framework deps)."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from football_agent.openclaw_bridge.enricher import BridgeEnricher
from football_agent.openclaw_bridge.models import BridgeMatchInput
from football_agent.services.enrichment_contract import ENRICHMENT_CONTEXT_PATH, ENRICHMENT_ODDS_PATH

logger = logging.getLogger(__name__)


def _parse_query(path: str) -> dict[str, str]:
    parsed = urlparse(path)
    raw = parse_qs(parsed.query, keep_blank_values=False)
    return {k: (v[0] if v else "") for k, v in raw.items()}


class OpenClawBridgeHandler(BaseHTTPRequestHandler):
    enricher_factory: Callable[[], BridgeEnricher] = BridgeEnricher  # type: ignore[assignment]

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self._json_response(
                200,
                {
                    "ok": True,
                    "status": "live",
                    "service": "openclaw_bridge",
                    "endpoints": ["/health", ENRICHMENT_CONTEXT_PATH, ENRICHMENT_ODDS_PATH],
                },
            )
            return
        if path == ENRICHMENT_CONTEXT_PATH:
            self._handle_context(_parse_query(self.path))
            return
        if path == ENRICHMENT_ODDS_PATH:
            self._handle_odds(_parse_query(self.path))
            return
        self._json_response(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json_response(400, {"error": "invalid_json"})
            return
        if not isinstance(data, dict):
            self._json_response(400, {"error": "body_must_be_object"})
            return

        params = {str(k): str(v) for k, v in data.items() if v is not None}
        if path == ENRICHMENT_CONTEXT_PATH:
            self._handle_context(params)
            return
        if path == ENRICHMENT_ODDS_PATH:
            self._handle_odds(params)
            return
        self._json_response(404, {"error": "not_found", "path": path})

    def _enricher(self) -> BridgeEnricher:
        # Class attribute — call via type(self) so lambdas are not bound to handler instance.
        factory = type(self).enricher_factory
        if factory is BridgeEnricher:
            return BridgeEnricher()
        return factory()

    def _handle_context(self, params: dict[str, str]) -> None:
        inp = BridgeMatchInput.from_params(params)
        envelope = self._enricher().enrich_context(inp)
        self._json_response(200, envelope.to_context_response())

    def _handle_odds(self, params: dict[str, str]) -> None:
        inp = BridgeMatchInput.from_params(params)
        enricher = self._enricher()
        envelope = enricher.enrich_odds(inp)
        self._json_response(200, envelope.payload)

    def _json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(
    host: str,
    port: int,
    *,
    enricher_factory: Optional[Callable[[], BridgeEnricher]] = None,
) -> ThreadingHTTPServer:
    handler = type(
        "ConfiguredBridgeHandler",
        (OpenClawBridgeHandler,),
        {"enricher_factory": enricher_factory or BridgeEnricher},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve_forever(host: str = "127.0.0.1", port: int = 8787, *, enricher_factory: Optional[Callable[[], BridgeEnricher]] = None) -> None:
    server = make_server(host, port, enricher_factory=enricher_factory)
    logger.info("OpenClaw bridge listening on http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down bridge")
    finally:
        server.server_close()
