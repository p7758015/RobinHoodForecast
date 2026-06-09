"""
Agent-side contract expectations for OpenClaw enrichment (context + odds).

No live backend is assumed — this module documents what football_agent expects
when a real OpenClaw endpoint becomes available.

Transport (v1 target)
---------------------
Split mode (default, ``OPENCLAW_ENRICHMENT_MODE=split``):

- ``GET {base}/v1/context?home=&away=&date=&competition=&kickoff_utc=``
- ``GET {base}/v1/odds?home=&away=&date=&match_id=&url=...``

Unified mode (future-ready, ``OPENCLAW_ENRICHMENT_MODE=unified``):

- ``GET {base}/v1/enrichment?...`` → combined payload with optional ``context`` and ``odds`` blocks.

Context block (maps to ``OpenClawMatchContext``)
-------------------------------------------------
Required for ``ok``:

- ``meta`` or top-level home/away/competition identifiers
- At least one substantive block among:
  ``squad_context``, ``motivation_narrative``, ``news``, ``fatigue_schedule_context``, ``coach_context``

Partial success:

- Valid JSON + identifiable match, but empty optional blocks → ``partial`` + extraction warnings.

Failure:

- HTTP error, timeout, auth error, empty body, or unparseable JSON → ``failed`` + reason.

Odds block (maps to ``MatchOddsContext``)
-----------------------------------------
Required for ``ok``:

- ``markets`` or equivalent market map with at least one priced outcome
- ``provenance`` / bookmaker hint optional but recommended

Partial success:

- Some markets present, ``missing_markets`` listed in provenance → still ``ok`` or ``partial`` depending on fill rate.

Failure:

- Same transport errors as context, or empty markets → ``failed``.

Pipeline behaviour matrix
-----------------------
| Situation                         | context | odds   | pipeline        |
|-----------------------------------|---------|--------|-----------------|
| OpenClaw not configured           | skip    | skip   | Flashscore-only |
| OpenClaw unreachable              | failed  | failed*| Flashscore-only |
| Context ok, odds empty            | ok      | failed | merge w/ context|
| Context empty, odds ok            | failed  | ok     | merge w/ odds   |
| Both ok (same base)               | ok      | ok     | full merge      |
| Separate ODDS_SERVICE_URL only    | skip**  | ok     | odds only       |

*When ``OPENCLAW_PROVIDES_ODDS=true`` (default), odds fetch uses the same base.
**Context still uses OpenClaw base when configured; only odds URL is separate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# HTTP paths (relative to enrichment base URL)
ENRICHMENT_UNIFIED_PATH = "/v1/enrichment"
ENRICHMENT_CONTEXT_PATH = "/v1/context"
ENRICHMENT_ODDS_PATH = "/v1/odds"

# Source status values (stored in pipeline ``sources`` dict)
SOURCE_OK = "ok"
SOURCE_FAILED = "failed"
SOURCE_SKIPPED = "skipped"
SOURCE_SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
SOURCE_PARTIAL = "partial"

# Enrichment routing modes
ENRICHMENT_MODE_NOT_CONFIGURED = "not_configured"
ENRICHMENT_MODE_SPLIT = "openclaw_split"
ENRICHMENT_MODE_UNIFIED = "openclaw_unified"
ENRICHMENT_MODE_ODDS_SEPARATE = "odds_separate"

ODDS_SOURCE_NONE = "none"
ODDS_SOURCE_OPENCLAW = "openclaw"
ODDS_SOURCE_SEPARATE = "separate_service"


def parse_unified_enrichment_payload(
    data: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
    """
    Split a combined OpenClaw ``/v1/enrichment`` response into raw context/odds dicts.

    Accepts wrapped payloads (``data`` / ``payload`` / ``result``) and block aliases.
    """
    warnings: List[str] = []
    if not isinstance(data, dict):
        warnings.append("enrichment_unified_bad_payload")
        return None, None, warnings

    inner = data
    for key in ("payload", "data", "result", "enrichment"):
        block = data.get(key)
        if isinstance(block, dict):
            inner = block
            break

    context_raw = inner.get("context") or inner.get("openclaw_context")
    odds_raw = inner.get("odds") or inner.get("odds_context")

    if context_raw is not None and not isinstance(context_raw, dict):
        warnings.append("enrichment_unified_context_not_object")
        context_raw = None
    if odds_raw is not None and not isinstance(odds_raw, dict):
        warnings.append("enrichment_unified_odds_not_object")
        odds_raw = None

    if context_raw is None and odds_raw is None:
        warnings.append("enrichment_unified_empty")

    if context_raw and not _context_has_signal(context_raw):
        warnings.append("enrichment_unified_context_empty_blocks")
    if odds_raw and not _odds_has_signal(odds_raw):
        warnings.append("enrichment_unified_odds_empty_markets")

    return context_raw, odds_raw, warnings


def _context_has_signal(raw: Dict[str, Any]) -> bool:
    for key in (
        "squad_context",
        "motivation_narrative",
        "news",
        "fatigue_schedule_context",
        "coach_context",
    ):
        block = raw.get(key)
        if isinstance(block, dict) and block:
            return True
    return False


def _odds_has_signal(raw: Dict[str, Any]) -> bool:
    markets = raw.get("markets") or raw.get("market_odds") or {}
    if isinstance(markets, dict) and any(markets.values()):
        return True
    return bool(raw.get("best_market") or raw.get("home_win_odds"))
