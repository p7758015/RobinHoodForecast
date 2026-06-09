"""Smoke/debug utility tests for OpenClaw enrichment (mock/fixture only)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from football_agent.debug import enrichment_smoke
from football_agent.debug.enrichment_diagnostics import (
    build_smoke_diagnostic,
    format_smoke_summary,
    redact_secrets,
)
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.services.enrichment_config import EnrichmentRouting
from football_agent.services.enrichment_contract import (
    ENRICHMENT_MODE_NOT_CONFIGURED,
    ENRICHMENT_MODE_SPLIT,
    ENRICHMENT_MODE_UNIFIED,
    ODDS_SOURCE_NONE,
    ODDS_SOURCE_OPENCLAW,
    SOURCE_FAILED,
    SOURCE_OK,
    SOURCE_PARTIAL,
    SOURCE_SKIPPED_NOT_CONFIGURED,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult

FIXTURES = Path(__file__).parent / "data"


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="smoke1",
            source_url="https://example.com/m",
            competition_name="Test League",
            home_team_name="Home FC",
            away_team_name="Away FC",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def _routing(
    *,
    base: str | None = "http://openclaw.local",
    mode: str = ENRICHMENT_MODE_SPLIT,
    odds_source: str = ODDS_SOURCE_OPENCLAW,
) -> EnrichmentRouting:
    return EnrichmentRouting(
        openclaw_base_url=base,
        context_base_url=base,
        odds_base_url=base if odds_source == ODDS_SOURCE_OPENCLAW else None,
        enrichment_mode=mode,
        odds_source=odds_source,
        odds_separate_service=False,
        openclaw_provides_odds=True,
        configured=bool(base),
    )


def _result(
    *,
    context=None,
    odds=None,
    openclaw: str = SOURCE_SKIPPED_NOT_CONFIGURED,
    odds_status: str = SOURCE_SKIPPED_NOT_CONFIGURED,
    warnings=None,
    routing: EnrichmentRouting | None = None,
) -> EnrichmentFetchResult:
    r = routing or _routing(base=None, mode=ENRICHMENT_MODE_NOT_CONFIGURED, odds_source=ODDS_SOURCE_NONE)
    return EnrichmentFetchResult(
        context=context,
        odds=odds,
        sources={
            "openclaw": openclaw,
            "odds": odds_status,
            "enrichment_backend": "openclaw" if r.configured else "none",
        },
        warnings=warnings or [],
        routing=r,
    )


def test_smoke_not_configured() -> None:
    with patch(
        "football_agent.debug.enrichment_smoke.fetch_enrichment_for_facts",
        return_value=_result(warnings=["enrichment_not_configured"]),
    ):
        diag = enrichment_smoke.run_smoke(
            home="Home FC",
            away="Away FC",
            mode="auto",
        )
    assert diag["enrichment"]["configured"] is False
    assert diag["status"]["completeness"] == "not_configured"
    text = format_smoke_summary(diag)
    assert "NOT configured" in text


def test_smoke_split_success() -> None:
    from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
    from football_agent.openclaw_context.service import OpenClawContextIngestionService
    from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
    from football_agent.odds.service import OddsIngestionService

    ctx = OpenClawContextIngestionService(
        FixtureFileOpenClawContextAdapter(FIXTURES),
    ).get_context_for_fixture("openclaw_context_sample")
    odds = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES)).get_odds_for_fixture(
        "odds_botola_sample_match",
    )
    assert ctx is not None
    assert odds is not None

    with patch(
        "football_agent.debug.enrichment_smoke.fetch_enrichment_for_facts",
        return_value=_result(
            context=ctx,
            odds=odds,
            openclaw=SOURCE_OK,
            odds_status=SOURCE_OK,
            routing=_routing(),
        ),
    ):
        diag = enrichment_smoke.run_smoke(
            facts_fixture="flashscore_botola_sample_match",
            fixtures_dir=FIXTURES,
            openclaw_url="http://openclaw.local",
            mode="split",
            verbose=True,
        )

    assert diag["status"]["context"] == SOURCE_OK
    assert diag["status"]["odds"] == SOURCE_OK
    assert diag["status"]["completeness"] == "full"
    assert diag["enrichment"]["odds_source"] == "openclaw"
    assert "http://openclaw.local/v1/context" in diag["enrichment"]["endpoints"]["context"]
    text = format_smoke_summary(diag)
    assert "Context status: ok" in text
    assert "Odds status: ok" in text


def test_smoke_unified_partial_response() -> None:
    with patch(
        "football_agent.debug.enrichment_smoke.fetch_enrichment_for_facts",
        return_value=_result(
            openclaw=SOURCE_PARTIAL,
            odds_status=SOURCE_FAILED,
            warnings=[
                "enrichment_unified_context_empty_blocks",
                "enrichment_partial:context_without_odds",
            ],
            routing=_routing(mode=ENRICHMENT_MODE_UNIFIED),
        ),
    ):
        diag = enrichment_smoke.run_smoke(
            home="H",
            away="A",
            openclaw_url="http://openclaw.local",
            mode="unified",
        )

    assert diag["status"]["completeness"] == "partial"
    assert "context_ok_odds_missing" in diag["contract"]["issues"]
    assert "http://openclaw.local/v1/enrichment" in diag["enrichment"]["endpoints"]["unified"]


def test_split_fallback_diagnostic() -> None:
    diag = build_smoke_diagnostic(
        facts=_facts(),
        result=_result(
            openclaw=SOURCE_OK,
            odds_status=SOURCE_OK,
            warnings=["enrichment_unified_fallback_split", "enrichment_unified_fetch_failed:timeout"],
            routing=_routing(),
        ),
        mode_requested="unified",
    )
    assert diag["split_fallback"] is True
    assert "unified_fallback_split" in diag["failure_reasons"]
    text = format_smoke_summary(diag)
    assert "split fallback" in text


def test_redact_secrets_in_output() -> None:
    raw = "Authorization: Bearer secret-token-123 api_key=abc123"
    redacted = redact_secrets(raw)
    assert "secret-token-123" not in redacted
    assert "abc123" not in redacted

    diag = build_smoke_diagnostic(
        facts=_facts(),
        result=_result(
            warnings=["odds_detail: HTTP 401 unauthorized api_key=LEAKED"],
            routing=_routing(base=None, mode=ENRICHMENT_MODE_NOT_CONFIGURED, odds_source=ODDS_SOURCE_NONE),
        ),
        mode_requested="auto",
    )
    payload = json.dumps(diag)
    assert "LEAKED" not in payload


@patch("football_agent.debug.enrichment_smoke.fetch_enrichment_for_facts")
def test_cli_main_json_not_configured(mock_fetch) -> None:
    mock_fetch.return_value = _result(warnings=["enrichment_not_configured"])
    code = enrichment_smoke.main(["--home", "H", "--away", "A", "--json"])
    assert code == 0


@patch("football_agent.debug.enrichment_smoke.fetch_enrichment_for_facts")
def test_cli_main_failed_exit_code(mock_fetch) -> None:
    mock_fetch.return_value = _result(
        openclaw=SOURCE_FAILED,
        odds_status=SOURCE_FAILED,
        warnings=["openclaw_context_fetch_failed:timeout"],
        routing=_routing(),
    )
    code = enrichment_smoke.main(["--home", "H", "--away", "A", "--openclaw-url", "http://oc"])
    assert code == 1


def test_cli_main_missing_input_exit_2() -> None:
    code = enrichment_smoke.main([])
    assert code == 2
