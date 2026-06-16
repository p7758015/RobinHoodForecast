"""CLI wiring for live_analysis_trace (no real HTTP)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from football_agent.debug import live_analysis_trace
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.tests.test_scorer_v2 import make_snapshot


def _scored_mock():
    scored = MagicMock()
    scored.snapshot = make_snapshot()
    scored.prediction = MagicMock()
    scored.prediction.best_market = None
    scored.prediction.market_predictions = []
    scored.prediction.overall_confidence_score = 0.5
    scored.prediction.express_safety = MagicMock()
    scored.prediction.express_safety.model_dump.return_value = {}
    scored.build_report = MagicMock()
    scored.build_report.merge_warnings = []
    scored.build_report.merge_missing_blocks = ["openclaw_context"]
    scored.build_report.openclaw_link_strategy = "unlinked"
    scored.build_report.odds_link_strategy = "unlinked"
    scored.build_report.builder_warnings = []
    scored.build_report.id_generation_notes = {}
    scored.scoring_warnings = []
    return scored


def _ok_result(**kwargs) -> LivePipelineResult:
    defaults = dict(
        success=True,
        path="flashscore_url",
        scored_run=_scored_mock(),
        sources={"flashscore": "ok", "openclaw": "failed"},
        warnings=["openclaw_context_fetch_failed: down"],
        openclaw_link_strategy="unlinked",
        odds_link_strategy="unlinked",
    )
    defaults.update(kwargs)
    return LivePipelineResult(**defaults)


def test_main_check_services() -> None:
    with patch(
        "football_agent.debug.live_analysis_trace.check_live_services",
        return_value=[],
    ):
        code = live_analysis_trace.main(["--check-services", "--json"])
    assert code == 0


def test_main_missing_flashscore_url_exit_2() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", None):
        code = live_analysis_trace.main(["--match-url", "https://example.com/m"])
    assert code == 2


def test_main_missing_input_exit_2() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        code = live_analysis_trace.main([])
    assert code == 2


def test_main_flashscore_fail_exit_1() -> None:
    fail = LivePipelineResult(
        success=False,
        path="flashscore_url",
        stage_failed="flashscore_ingest",
        user_message="down",
        sources={"flashscore": "failed"},
    )
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        with patch.object(live_analysis_trace.LiveFlashscorePipeline, "analyze_flashscore_url", return_value=fail):
            code = live_analysis_trace.main(["--match-url", "https://example.com/m"])
    assert code == 1


def test_main_openclaw_fail_continues_exit_0(tmp_path: Path) -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        with patch.object(
            live_analysis_trace.LiveFlashscorePipeline,
            "analyze_flashscore_url",
            return_value=_ok_result(),
        ):
            code = live_analysis_trace.main(
                [
                    "--flashscore-id",
                    "Sfgk1gCs",
                    "--use-openclaw",
                    "--no-persist",
                    "--json",
                ]
            )
    assert code == 0


def test_main_evaluate_requires_db_path() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        with patch.object(
            live_analysis_trace.LiveFlashscorePipeline,
            "analyze_flashscore_url",
            return_value=_ok_result(persisted=True, run_id="r1", match_key="mk1"),
        ):
            with patch.object(live_analysis_trace.OfflineEvaluationServiceV2, "evaluate", return_value={"counts": {}}):
                code = live_analysis_trace.main(
                    [
                        "--match-url",
                        "https://example.com/m",
                        "--db-path",
                        "football_agent/data/football_agent.db",
                        "--evaluate",
                        "--json",
                    ]
                )
    assert code == 0
