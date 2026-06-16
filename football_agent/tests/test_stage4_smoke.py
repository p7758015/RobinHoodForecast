"""Stage 4 operational smoke helper tests (mock only)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent.debug.live_service_health import ServiceHealth, check_flashscore_scraper
from football_agent.debug.stage4_smoke import SCENARIOS, STAGE4_SMOKE_MATCHES, main, run_smoke
from football_agent.services.live_flashscore_pipeline import LivePipelineResult


def test_stage4_smoke_matches_canonical_urls() -> None:
    assert "avai" in STAGE4_SMOKE_MATCHES
    assert "6FiXiHcc" in STAGE4_SMOKE_MATCHES["avai"]["match_url"]
    assert "vs5vjeS9" in STAGE4_SMOKE_MATCHES["goias"]["flashscore_id"]
    assert "zDA7ydDG" in STAGE4_SMOKE_MATCHES["athletic"]["match_url"]


@patch("football_agent.debug.live_service_health._probe_get")
def test_check_flashscore_scraper_ok(mock_probe) -> None:
    mock_probe.return_value = ServiceHealth(
        name="flashscore_scraper",
        url="http://localhost:3000/health",
        ok=True,
        status_code=200,
    )
    with patch("football_agent.debug.live_service_health.config.FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        h = check_flashscore_scraper()
    assert h.ok is True


@patch("football_agent.debug.stage4_smoke.LiveFlashscorePipeline")
def test_run_smoke_flashscore_only(mock_pipeline_cls) -> None:
    scored = MagicMock()
    scored.build_report.merge_missing_blocks = ["openclaw_context", "odds_context"]
    scored.build_report.openclaw_link_strategy = "unlinked"
    scored.build_report.odds_link_strategy = "unlinked"
    scored.build_report.merge_warnings = []
    scored.build_report.builder_warnings = []
    scored.build_report.id_generation_notes = {}
    scored.snapshot.match_meta.model_dump.return_value = {"competition_name": "Serie B"}
    scored.snapshot.source_tags = []
    scored.prediction.best_market = None
    scored.prediction.market_predictions = []
    scored.prediction.overall_confidence_score = 0.5
    scored.prediction.express_safety.model_dump.return_value = {}
    scored.scoring_warnings = []

    mock_pipeline_cls.return_value.analyze_flashscore_url.return_value = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=scored,
        sources={"flashscore": "ok", "openclaw": "skipped_not_configured"},
    )

    out = run_smoke(scenario_name="flashscore-only", match_key="avai", as_json=True)
    assert out["exit_code"] == 0
    assert out["payload"]["all_success"] is True
    assert mock_pipeline_cls.return_value.analyze_flashscore_url.called


def test_main_check_services_exit_code() -> None:
    with patch(
        "football_agent.debug.stage4_smoke.check_live_services",
        return_value=[
            ServiceHealth("flashscore_scraper", "http://localhost:3000/health", ok=True),
            ServiceHealth("openclaw_gateway", "", ok=False, error="not configured"),
        ],
    ):
        code = main(["--check-services"])
    assert code == 1


def test_scenarios_cover_operational_modes() -> None:
    assert "flashscore-only" in SCENARIOS
    assert "flashscore-openclaw" in SCENARIOS
    assert "openclaw-degraded" in SCENARIOS
    assert "persist-eval" in SCENARIOS
