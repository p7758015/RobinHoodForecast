"""CLI wiring for live_analysis_trace (no real HTTP)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from football_agent.debug import live_analysis_trace
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.tests.test_scorer_v2 import make_snapshot


def _minimal_facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="fs-cli",
            source_url="https://example.com/m",
            competition_name="Test League",
            home_team_name="Home FC",
            away_team_name="Away FC",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def test_main_missing_flashscore_url_exit_2() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", None):
        code = live_analysis_trace.main(["--match-url", "https://example.com/m"])
    assert code == 2


def test_main_missing_input_exit_2() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        code = live_analysis_trace.main([])
    assert code == 2


def test_main_flashscore_fail_exit_1() -> None:
    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        with patch.object(
            live_analysis_trace,
            "_fetch_flashscore_facts",
            return_value=(None, {"flashscore": "failed", "flashscore_error": "down"}),
        ):
            code = live_analysis_trace.main(["--match-url", "https://example.com/m"])
    assert code == 1


def test_main_openclaw_fail_continues_exit_0(tmp_path: Path) -> None:
    facts = _minimal_facts()
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
    scored.build_report.merge_missing_blocks = []
    scored.build_report.openclaw_link_strategy = "unlinked"
    scored.build_report.odds_link_strategy = "unlinked"
    scored.build_report.builder_warnings = []
    scored.build_report.id_generation_notes = {}
    scored.scoring_warnings = []

    with patch.object(live_analysis_trace.config, "FLASHSCORE_SCRAPER_URL", "http://localhost:3000"):
        with patch.object(live_analysis_trace.config, "OPENCLAW_CONTEXT_BASE_URL", "http://oc.local"):
            with patch.object(
                live_analysis_trace,
                "_fetch_flashscore_facts",
                return_value=(facts, {"flashscore": "ok"}),
            ):
                with patch.object(
                    live_analysis_trace,
                    "fetch_openclaw_context_for_facts",
                    return_value=(
                        None,
                        {"openclaw": "failed"},
                        ["openclaw_context_fetch_failed: down"],
                    ),
                ):
                    with patch.object(live_analysis_trace, "_fetch_odds_fixture", return_value=(None, {"odds": "none"})):
                        with patch.object(live_analysis_trace, "merge_match_context_v2", return_value=MagicMock()):
                            with patch.object(
                                live_analysis_trace.MergedSnapshotBuilderV2,
                                "build_with_report",
                                return_value=(make_snapshot(), MagicMock()),
                            ):
                                with patch.object(
                                    live_analysis_trace.ScoringServiceV2,
                                    "score_snapshot_with_report",
                                    return_value=scored,
                                ):
                                    code = live_analysis_trace.main(
                                        [
                                            "--match-url",
                                            "https://example.com/m",
                                            "--no-persist",
                                            "--json",
                                        ]
                                    )
    assert code == 0
