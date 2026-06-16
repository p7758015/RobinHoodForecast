"""
Live enrichment backend client for OpenClaw bridge.

Tries OpenAI-compatible ``/v1/chat/completions`` (and optionally ``/v1/responses``)
on the OpenClaw gateway. Returns structured JSON only — never free text upstream.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urljoin

import requests

from football_agent.openclaw_bridge.models import BridgeMatchInput

logger = logging.getLogger(__name__)

CONTEXT_BLOCK_KEYS = (
    "news",
    "squad_context",
    "coach_context",
    "motivation_narrative",
    "fatigue_schedule_context",
)

ODDS_MARKET_KEYS = (
    "home_win",
    "away_win",
    "double_chance_1x",
    "double_chance_x2",
    "btts_yes",
    "home_team_to_score_yes",
    "away_team_to_score_yes",
    "over_1_5",
    "under_3_5",
)

CONTEXT_SYSTEM_PROMPT = """You are a football match analyst for league fixtures.
Return ONLY a single valid JSON object (no markdown, no commentary).
Use the exact top-level keys shown in the user message.
If information is uncertain, use LOW confidence and brief summaries.
Do not invent specific player names unless reasonably inferable from public knowledge.
League-match scope only — ignore cups and national teams."""

CONTEXT_USER_TEMPLATE = """Analyze this upcoming league match and fill context blocks.

Match:
- home: {home_team}
- away: {away_team}
- competition: {competition}
- date: {date}
- kickoff_utc: {kickoff_utc}
- country: {country}
- match_url: {match_url}
- home_form_hint: {home_form}
- away_form_hint: {away_form}
- standings_hint: {standings}

Return JSON with these top-level keys only:
{{
  "news": {{
    "match_news_items": [{{"title": str, "summary": str, "source_name": str, "reliability_level": "LOW|MEDIUM|HIGH", "affects_team": "HOME|AWAY|BOTH"}}]
  }},
  "squad_context": {{
    "home": {{"missing_players_context": [], "lineup_uncertainty_notes": [str], "depth_risk_level": str, "rotation_risk_level": str}},
    "away": {{"missing_players_context": [], "lineup_uncertainty_notes": [str], "depth_risk_level": str, "rotation_risk_level": str}}
  }},
  "coach_context": {{
    "home": {{"coach_name": str|null, "influence_summary": str, "pressure_summary": str}},
    "away": {{"coach_name": str|null, "influence_summary": str, "pressure_summary": str}},
    "matchup": {{"coach_vs_coach_summary": str, "confidence": "LOW|MEDIUM|HIGH"}}
  }},
  "motivation_narrative": {{
    "home": {{"primary_objective_summary": str, "pressure_summary": str, "confidence": "LOW|MEDIUM|HIGH"}},
    "away": {{"primary_objective_summary": str, "pressure_summary": str, "confidence": "LOW|MEDIUM|HIGH"}},
    "matchwide": {{"public_narrative_summary": str, "confidence": "LOW|MEDIUM|HIGH"}}
  }},
  "fatigue_schedule_context": {{
    "home": {{"fatigue_summary": str, "rotation_expectation_summary": str, "confidence": "LOW|MEDIUM|HIGH"}},
    "away": {{"fatigue_summary": str, "travel_summary": str, "confidence": "LOW|MEDIUM|HIGH"}}
  }}
}}"""

ODDS_SYSTEM_PROMPT = """You are a football odds analyst.
Return ONLY a single valid JSON object with decimal odds (European format).
Use plausible market estimates when exact bookmaker prices are unknown.
Mark confidence LOW when estimated."""

ODDS_USER_TEMPLATE = """Estimate baseline decimal odds for this league match.

Match:
- home: {home_team}
- away: {away_team}
- competition: {competition}
- date: {date}

Return JSON:
{{
  "markets": {{
    "home_win": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}},
    "away_win": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}},
    "double_chance_1x": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}},
    "double_chance_x2": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}},
    "over_1_5": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}},
    "btts_yes": {{"odds_value": float, "bookmaker_name": str, "confidence": "LOW|MEDIUM"}}
  }}
}}"""


@dataclass
class LiveBackendResult:
    data: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    raw_snippet: Optional[str] = None
    source: str = "unknown"


class LiveBackendClient(Protocol):
    def fetch_context_blocks(self, inp: BridgeMatchInput) -> LiveBackendResult: ...

    def fetch_odds_markets(self, inp: BridgeMatchInput) -> LiveBackendResult: ...


class NoopLiveBackend:
    """Test double — always unavailable."""

    def fetch_context_blocks(self, inp: BridgeMatchInput) -> LiveBackendResult:
        return LiveBackendResult(warnings=["backend_unavailable"], source="noop")

    def fetch_odds_markets(self, inp: BridgeMatchInput) -> LiveBackendResult:
        return LiveBackendResult(warnings=["backend_unavailable"], source="noop")


class OpenClawChatBackend:
    """OpenAI-compatible chat backend on OpenClaw gateway."""

    def __init__(
        self,
        gateway_url: str,
        *,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        chat_path: str = "/v1/chat/completions",
        responses_path: str = "/v1/responses",
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._gateway_url = gateway_url.strip().rstrip("/")
        self._api_key = (api_key or "").strip() or None
        self._model = model
        self._chat_path = chat_path
        self._responses_path = responses_path
        self._timeout = timeout_s
        self._session = session or requests.Session()
        if self._api_key:
            self._session.headers.setdefault("Authorization", f"Bearer {self._api_key}")

    def fetch_context_blocks(self, inp: BridgeMatchInput) -> LiveBackendResult:
        user = CONTEXT_USER_TEMPLATE.format(
            home_team=inp.home_team,
            away_team=inp.away_team,
            competition=inp.competition_name or "league",
            date=inp.date or "",
            kickoff_utc=inp.kickoff_utc or "",
            country=inp.country or "",
            match_url=inp.match_url or "",
            home_form=inp.home_form_summary or "",
            away_form=inp.away_form_summary or "",
            standings=inp.standings_summary or "",
        )
        result = self._call_llm(CONTEXT_SYSTEM_PROMPT, user)
        if not result.data:
            return result
        blocks = {k: result.data[k] for k in CONTEXT_BLOCK_KEYS if isinstance(result.data.get(k), dict)}
        if not blocks:
            result.warnings.append("backend_invalid_payload")
            result.data = None
            return result
        result.data = blocks
        result.source = "openclaw_chat"
        return result

    def fetch_odds_markets(self, inp: BridgeMatchInput) -> LiveBackendResult:
        user = ODDS_USER_TEMPLATE.format(
            home_team=inp.home_team,
            away_team=inp.away_team,
            competition=inp.competition_name or "league",
            date=inp.date or "",
        )
        result = self._call_llm(ODDS_SYSTEM_PROMPT, user)
        if not result.data:
            return result
        markets = result.data.get("markets") if isinstance(result.data.get("markets"), dict) else result.data
        if not isinstance(markets, dict) or not any(markets.values()):
            result.warnings.append("backend_invalid_payload")
            result.data = None
            return result
        result.data = {"markets": markets}
        result.source = "openclaw_chat"
        return result

    def _call_llm(self, system: str, user: str) -> LiveBackendResult:
        warnings: List[str] = []
        for path, label in ((self._chat_path, "chat_completions"), (self._responses_path, "responses")):
            try:
                data, snippet, path_warnings = self._post_endpoint(path, system, user, label)
                warnings.extend(path_warnings)
                if data:
                    return LiveBackendResult(data=data, warnings=warnings, raw_snippet=snippet, source=label)
            except requests.Timeout:
                warnings.append("backend_timeout")
            except requests.RequestException as exc:
                warnings.append(f"backend_unavailable:{exc}")
        if not warnings:
            warnings.append("backend_unavailable")
        return LiveBackendResult(warnings=warnings, source="openclaw_chat")

    def _post_endpoint(
        self,
        path: str,
        system: str,
        user: str,
        label: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
        warnings: List[str] = []
        url = urljoin(self._gateway_url + "/", path.lstrip("/"))

        if path.endswith("responses"):
            payload: Dict[str, Any] = {
                "model": self._model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        else:
            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }

        resp = self._session.post(url, json=payload, timeout=self._timeout)
        text = (resp.text or "")[:1200]

        if resp.status_code == 404:
            warnings.append(f"backend_endpoint_not_found:{label}")
            return None, text, warnings
        if resp.status_code >= 400:
            warnings.append(f"backend_http_{resp.status_code}")
            return None, text, warnings
        if "OpenClaw Control" in text or text.lstrip().startswith("<!"):
            warnings.append("backend_returns_html_not_json")
            return None, text, warnings

        try:
            envelope = resp.json()
        except ValueError:
            warnings.append("backend_invalid_payload")
            return None, text, warnings

        content = self._extract_message_content(envelope)
        if not content:
            warnings.append("backend_empty_content")
            return None, text, warnings

        parsed = self._parse_json_content(content)
        if not parsed:
            warnings.append("backend_invalid_payload")
            return None, content[:500], warnings

        return parsed, content[:500], warnings

    @staticmethod
    def _extract_message_content(envelope: Dict[str, Any]) -> Optional[str]:
        choices = envelope.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

        output = envelope.get("output")
        if isinstance(output, list):
            parts: List[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            parts.append(str(block.get("text") or ""))
                elif isinstance(content, str):
                    parts.append(content)
            joined = "".join(parts).strip()
            if joined:
                return joined

        for key in ("content", "text", "response"):
            val = envelope.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @staticmethod
    def _parse_json_content(content: str) -> Optional[Dict[str, Any]]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if len(lines) > 2 else lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None
        return data if isinstance(data, dict) else None
