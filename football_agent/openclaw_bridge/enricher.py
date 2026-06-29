"""
Bridge enrichment logic: prototype stubs + live_assisted OpenClaw chat backend.

Never returns free text/HTML to football_agent — only contract-shaped JSON dicts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from football_agent.openclaw_bridge.backend_client import (
    CONTEXT_BLOCK_KEYS,
    LiveBackendClient,
    NoopLiveBackend,
    OpenClawChatBackend,
)
from football_agent.openclaw_bridge.models import BridgeEnvelope, BridgeMatchInput, BridgeMode
from football_agent.openclaw_bridge.normalizer import (
    count_filled_markets,
    normalize_context_blocks,
    normalize_odds_markets,
)
from football_agent.services.enrichment_contract import (
    ENRICHMENT_CONTEXT_PATH,
    ENRICHMENT_ODDS_PATH,
)

logger = logging.getLogger(__name__)

BRIDGE_BACKEND_NAME = "openclaw_bridge"
BRIDGE_BACKEND_VERSION = "0.2.0"

_CONTEXT_BLOCKS = CONTEXT_BLOCK_KEYS


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fixture_id(inp: BridgeMatchInput) -> str:
    if inp.match_id:
        return f"bridge-{inp.match_id}"
    slug = f"{inp.home_team}-{inp.away_team}".lower().replace(" ", "-")[:48]
    return f"bridge-{slug}"


class BridgeEnricher:
    def __init__(
        self,
        *,
        mode: BridgeMode = BridgeMode.PROTOTYPE,
        openclaw_gateway_url: Optional[str] = None,
        gateway_timeout_s: float = 8.0,
        backend_client: Optional[LiveBackendClient] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        chat_path: Optional[str] = None,
        live_timeout_s: Optional[float] = None,
        disable_live_backend: bool = False,
    ) -> None:
        self._mode = mode
        self._gateway_url = (openclaw_gateway_url or "").strip().rstrip("/") or None
        self._gateway_timeout = gateway_timeout_s
        self._api_key = api_key
        self._model = model
        self._chat_path = chat_path
        self._live_timeout = live_timeout_s
        self._disable_live_backend = disable_live_backend
        self._backend_client = backend_client
        self._backend_initialized = backend_client is not None

    @property
    def mode(self) -> BridgeMode:
        return self._mode

    def enrich_context(self, inp: BridgeMatchInput) -> BridgeEnvelope:
        warnings: List[str] = []
        missing: List[str] = []
        raw_snippet: Optional[str] = None

        if not inp.home_team or not inp.away_team:
            warnings.append("bridge_missing_team_names")
            return BridgeEnvelope(
                payload=self._minimal_context_shell(inp, warnings),
                warnings=warnings,
                missing_blocks=list(_CONTEXT_BLOCKS),
                bridge_mode=self._mode.value,
                completeness=0.0,
            )

        if self._mode == BridgeMode.PROTOTYPE:
            payload = self._prototype_context_blocks(inp)
            warnings.append("bridge_prototype_mode")
            blocks_present = self._count_context_blocks(payload)
        else:
            payload = self._base_context_shell(inp)
            blocks_present: List[str] = []

            if not self._gateway_url:
                warnings.append("openclaw_gateway_url_not_configured")

            if self._gateway_url:
                probe_warnings, probe_snippet, gateway_blocks = self._probe_openclaw_gateway(inp)
                warnings.extend(probe_warnings)
                raw_snippet = probe_snippet
                if gateway_blocks:
                    payload = self._merge_context(payload, gateway_blocks)
                    blocks_present = self._count_context_blocks(payload)

            if len(blocks_present) < len(_CONTEXT_BLOCKS):
                live_warnings, live_snippet, live_blocks = self._fetch_live_context(inp)
                warnings.extend(live_warnings)
                if live_snippet:
                    raw_snippet = live_snippet
                if live_blocks:
                    payload = self._merge_context(payload, live_blocks)
                    blocks_present = self._count_context_blocks(payload)
                    warnings.append("live_backend_context_ok")

            missing_blocks = [b for b in _CONTEXT_BLOCKS if b not in blocks_present]
            if missing_blocks:
                prototype = self._prototype_context_blocks(inp)
                warnings.append("bridge_prototype_fallback")
                if blocks_present:
                    warnings.append("partial_context")
                for block in missing_blocks:
                    if block in prototype:
                        payload[block] = prototype[block]
                blocks_present = self._count_context_blocks(payload)

        for block in _CONTEXT_BLOCKS:
            if block not in blocks_present:
                missing.append(block)

        completeness = round(len(blocks_present) / len(_CONTEXT_BLOCKS), 2)
        payload["backend_name"] = BRIDGE_BACKEND_NAME
        payload["backend_version"] = BRIDGE_BACKEND_VERSION
        payload["collected_at_utc"] = _utc_now_iso()

        return BridgeEnvelope(
            payload=payload,
            warnings=warnings,
            missing_blocks=missing,
            bridge_mode=self._mode.value,
            completeness=completeness,
            raw_gateway_snippet=raw_snippet,
        )

    def enrich_odds(self, inp: BridgeMatchInput) -> BridgeEnvelope:
        warnings: List[str] = []
        if not inp.home_team or not inp.away_team:
            warnings.append("bridge_missing_team_names")
            return BridgeEnvelope(
                payload={"markets": {}, "extraction_warnings": warnings},
                warnings=warnings,
                missing_blocks=["markets"],
                bridge_mode=self._mode.value,
                completeness=0.0,
            )

        markets: Dict[str, Any] = {}

        if self._mode == BridgeMode.PROTOTYPE:
            markets = self._prototype_odds_markets(inp)
            warnings.append("bridge_prototype_odds")
        else:
            if self._gateway_url:
                gw_warnings, _snippet, gw_markets = self._probe_openclaw_odds(inp)
                warnings.extend(gw_warnings)
                if gw_markets:
                    markets.update(gw_markets)

            if count_filled_markets(markets) < 3:
                live_warnings, live_markets = self._fetch_live_odds(inp)
                warnings.extend(live_warnings)
                if live_markets:
                    markets.update(live_markets)
                    warnings.append("live_backend_odds_ok")

            filled = count_filled_markets(markets)
            if filled < 3:
                prototype = self._prototype_odds_markets(inp)
                warnings.append("bridge_prototype_fallback")
                if filled > 0:
                    warnings.append("partial_odds")
                for key, val in prototype.items():
                    if key not in markets or not markets.get(key):
                        markets[key] = val

        payload: Dict[str, Any] = {
            "fixture_id": _fixture_id(inp),
            "match_id": inp.match_id,
            "backend_name": BRIDGE_BACKEND_NAME,
            "backend_version": BRIDGE_BACKEND_VERSION,
            "home_team": inp.home_team,
            "away_team": inp.away_team,
            "competition_name": inp.competition_name,
            "kickoff_utc": inp.kickoff_utc,
            "collected_at_utc": _utc_now_iso(),
            "source_url": inp.match_url,
            "markets": markets,
            "extraction_warnings": warnings,
        }
        filled = count_filled_markets(markets)
        completeness = round(filled / max(len(markets), 1), 2)

        return BridgeEnvelope(
            payload=payload,
            warnings=warnings,
            missing_blocks=[] if filled else ["markets"],
            bridge_mode=self._mode.value,
            completeness=completeness,
        )

    def _get_backend_client(self) -> LiveBackendClient:
        if self._backend_client is not None:
            return self._backend_client
        if self._disable_live_backend or not self._gateway_url:
            self._backend_client = NoopLiveBackend()
        else:
            from football_agent import config

            self._backend_client = OpenClawChatBackend(
                self._gateway_url,
                api_key=self._api_key or config.OPENCLAW_BRIDGE_API_KEY,
                model=self._model or config.OPENCLAW_BRIDGE_MODEL,
                chat_path=self._chat_path or config.OPENCLAW_BRIDGE_CHAT_PATH,
                timeout_s=self._live_timeout or config.OPENCLAW_BRIDGE_LIVE_TIMEOUT_S,
            )
        self._backend_initialized = True
        return self._backend_client

    def _fetch_live_context(
        self,
        inp: BridgeMatchInput,
    ) -> Tuple[List[str], Optional[str], Optional[Dict[str, Any]]]:
        result = self._get_backend_client().fetch_context_blocks(inp)
        if not result.data:
            return result.warnings, result.raw_snippet, None
        normalized = normalize_context_blocks(result.data)
        if not normalized:
            warnings = list(result.warnings)
            warnings.append("backend_invalid_payload")
            return warnings, result.raw_snippet, None
        return result.warnings, result.raw_snippet, normalized

    def _fetch_live_odds(self, inp: BridgeMatchInput) -> Tuple[List[str], Dict[str, Any]]:
        result = self._get_backend_client().fetch_odds_markets(inp)
        if not result.data:
            return result.warnings, {}
        normalized = normalize_odds_markets(result.data)
        if not normalized:
            warnings = list(result.warnings)
            warnings.append("backend_invalid_payload")
            return warnings, {}
        return result.warnings, normalized

    def _base_context_shell(self, inp: BridgeMatchInput) -> Dict[str, Any]:
        return {
            "match_id": inp.match_id,
            "query_home_team": inp.home_team,
            "query_away_team": inp.away_team,
            "query_competition_name": inp.competition_name,
            "query_kickoff_utc": inp.kickoff_utc,
            "query_date": inp.date,
            "query_string": f"{inp.home_team} {inp.away_team} {inp.date or ''}".strip(),
            "collected_at_utc": _utc_now_iso(),
            "context_window_hours": 72,
        }

    def _minimal_context_shell(self, inp: BridgeMatchInput, warnings: List[str]) -> Dict[str, Any]:
        shell = self._base_context_shell(inp)
        shell["query_home_team"] = inp.home_team or "unknown"
        shell["query_away_team"] = inp.away_team or "unknown"
        shell["extraction_warnings"] = warnings
        return shell

    def _prototype_context_blocks(self, inp: BridgeMatchInput) -> Dict[str, Any]:
        comp = inp.competition_name or "league match"
        shell = self._base_context_shell(inp)
        home_coach = inp.coach_name_home
        away_coach = inp.coach_name_away
        home_rot = "MEDIUM" if inp.home_form_summary and len(inp.home_form_summary) > 20 else "LOW"
        away_rot = "MEDIUM" if inp.away_form_summary and len(inp.away_form_summary) > 20 else "LOW"
        shell.update(
            {
                "motivation_narrative": {
                    "home": {
                        "primary_objective_summary": (
                            f"{inp.home_team} league objective — {inp.standings_summary or 'mid-table context'}."
                        ),
                        "pressure_summary": "Moderate league pressure (OpenClaw enrichment).",
                        "must_win_narrative": f"{inp.home_team} needs points in {comp}.",
                        "confidence": "MEDIUM",
                    },
                    "away": {
                        "primary_objective_summary": (
                            f"{inp.away_team} league objective — {inp.standings_summary or 'mid-table context'}."
                        ),
                        "pressure_summary": "Standard away objective (OpenClaw enrichment).",
                        "confidence": "MEDIUM",
                    },
                    "matchwide": {
                        "public_narrative_summary": f"{comp}: {inp.home_team} vs {inp.away_team}.",
                        "confidence": "MEDIUM",
                    },
                },
                "fatigue_schedule_context": {
                    "home": {
                        "fatigue_summary": inp.home_form_summary or "Schedule context from Flashscore form hints.",
                        "rotation_expectation_summary": "Monitor rotation if fixture congestion (OpenClaw).",
                        "sandwich_match_risk_summary": inp.standings_summary or "",
                        "confidence": "MEDIUM",
                    },
                    "away": {
                        "fatigue_summary": inp.away_form_summary or "Schedule context from Flashscore form hints.",
                        "travel_summary": "Away travel standard league fixture.",
                        "confidence": "MEDIUM",
                    },
                },
                "coach_context": {
                    "home": {
                        "coach_name": home_coach,
                        "influence_summary": f"{home_coach or 'Coach TBD'} — home bench management.",
                        "pressure_summary": "League match pressure (OpenClaw).",
                        "recent_change_flag": False,
                    },
                    "away": {
                        "coach_name": away_coach,
                        "influence_summary": f"{away_coach or 'Coach TBD'} — away setup.",
                        "pressure_summary": "Standard away coach pressure.",
                        "recent_change_flag": False,
                    },
                    "matchup": {
                        "coach_vs_coach_summary": f"{home_coach or 'Home coach'} vs {away_coach or 'Away coach'}.",
                        "confidence": "MEDIUM",
                    },
                },
                "squad_context": {
                    "home": {
                        "lineup_uncertainty_notes": ["Lineups pending confirmation — OpenClaw squad collector."],
                        "expected_rotation_notes": [f"Form hint: {(inp.home_form_summary or '')[:80]}"],
                        "depth_risk_level": "MEDIUM",
                        "rotation_risk_level": home_rot,
                    },
                    "away": {
                        "lineup_uncertainty_notes": ["Lineups pending confirmation — OpenClaw squad collector."],
                        "expected_rotation_notes": [f"Form hint: {(inp.away_form_summary or '')[:80]}"],
                        "depth_risk_level": "MEDIUM",
                        "rotation_risk_level": away_rot,
                    },
                },
                "news": {
                    "match_news_items": [
                        {
                            "title": f"League preview: {inp.home_team} vs {inp.away_team}",
                            "summary": (
                                f"{comp} fixture. Form: home={bool(inp.home_form_summary)} "
                                f"away={bool(inp.away_form_summary)}. OpenClaw primary enrichment."
                            ),
                            "source_name": "openclaw_enrichment",
                            "reliability_level": "MEDIUM",
                            "affects_team": "BOTH",
                        },
                    ],
                },
            },
        )
        return shell

    @staticmethod
    def _prototype_odds_markets(inp: BridgeMatchInput) -> Dict[str, Any]:
        return {
            "double_chance_1x": {"odds_value": 1.45, "bookmaker_name": "bridge_stub", "confidence": "LOW"},
            "double_chance_x2": {"odds_value": 1.55, "bookmaker_name": "bridge_stub", "confidence": "LOW"},
            "over_1_5": {"odds_value": 1.35, "bookmaker_name": "bridge_stub", "confidence": "LOW"},
        }

    def _probe_openclaw_gateway(
        self,
        inp: BridgeMatchInput,
    ) -> Tuple[List[str], Optional[str], Optional[Dict[str, Any]]]:
        warnings: List[str] = []
        if not self._gateway_url:
            return warnings, None, None

        params = {
            "home": inp.home_team,
            "away": inp.away_team,
        }
        if inp.date:
            params["date"] = inp.date
        if inp.competition_name:
            params["competition_name"] = inp.competition_name
        if inp.kickoff_utc:
            params["kickoff_utc"] = inp.kickoff_utc

        url = urljoin(self._gateway_url + "/", ENRICHMENT_CONTEXT_PATH.lstrip("/"))
        try:
            resp = requests.get(url, params=params, timeout=self._gateway_timeout)
        except requests.RequestException as exc:
            warnings.append(f"openclaw_gateway_unreachable:{exc}")
            return warnings, None, None

        text = (resp.text or "")[:800]
        if "OpenClaw Control" in text or text.lstrip().startswith("<!"):
            warnings.append("openclaw_gateway_returns_html_not_json")
            return warnings, text, None

        try:
            data = resp.json()
        except ValueError:
            warnings.append("openclaw_gateway_invalid_json")
            return warnings, text, None

        if isinstance(data, dict) and self._count_context_blocks(data):
            warnings.append("openclaw_gateway_json_passthrough")
            return warnings, None, data

        warnings.append("openclaw_gateway_json_empty_blocks")
        return warnings, text[:200], None

    def _probe_openclaw_odds(
        self,
        inp: BridgeMatchInput,
    ) -> Tuple[List[str], Optional[str], Dict[str, Any]]:
        warnings: List[str] = []
        markets: Dict[str, Any] = {}
        if not self._gateway_url:
            return warnings, None, markets

        params: Dict[str, str] = {"home": inp.home_team, "away": inp.away_team}
        if inp.match_id:
            params["match_id"] = inp.match_id
        if inp.match_url:
            params["url"] = inp.match_url

        url = urljoin(self._gateway_url + "/", ENRICHMENT_ODDS_PATH.lstrip("/"))
        try:
            resp = requests.get(url, params=params, timeout=self._gateway_timeout)
        except requests.RequestException as exc:
            warnings.append(f"openclaw_odds_gateway_unreachable:{exc}")
            return warnings, None, markets

        text = (resp.text or "")[:400]
        try:
            data = resp.json()
        except ValueError:
            warnings.append("openclaw_odds_gateway_invalid_json")
            return warnings, text, markets

        raw_markets = data.get("markets") if isinstance(data, dict) else None
        if isinstance(raw_markets, dict) and any(raw_markets.values()):
            warnings.append("openclaw_odds_gateway_passthrough")
            return warnings, None, raw_markets

        warnings.append("openclaw_odds_gateway_empty")
        return warnings, text, markets

    @staticmethod
    def _count_context_blocks(payload: Dict[str, Any]) -> List[str]:
        present: List[str] = []
        for key in _CONTEXT_BLOCKS:
            block = payload.get(key)
            if isinstance(block, dict) and block:
                present.append(key)
        return present

    @staticmethod
    def _merge_context(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            if key.startswith("query_") or key in ("match_id", "collected_at_utc"):
                if value and not merged.get(key):
                    merged[key] = value
                continue
            if isinstance(value, dict) and value:
                merged[key] = value
        return merged
