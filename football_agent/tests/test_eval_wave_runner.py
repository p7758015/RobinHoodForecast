"""Tests for eval wave operational runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from football_agent.eval_pool.wave_manifest import EvalWaveManifest, load_wave_manifest
from football_agent.eval_pool.wave_runner import EvalWaveRunner
from football_agent.eval_pool.wave_summary import build_wave_markdown
from football_agent.paths import PACKAGE_ROOT


def test_preset_manifest_loads() -> None:
    m = load_wave_manifest(preset="june18_21_first_batch")
    assert m.wave_name == "june18_21_first_batch"
    assert m.date_from == "2026-06-18"
    assert m.date_to == "2026-06-21"
    assert m.expected_matches == 41
    assert "kazakhstan_premier" in m.league_keys
    assert "finland_veikkausliiga" in m.league_keys


def test_extended_pool_keys_resolve() -> None:
    from football_agent.eval_pool.scope import filter_pool_keys, resolve_pool_entry

    keys = load_wave_manifest(preset="june18_21_first_batch").league_keys
    entries = filter_pool_keys(keys)
    assert len(entries) == len(keys)
    assert resolve_pool_entry("Botola Pro", "Morocco") is not None
    assert resolve_pool_entry("Veikkausliiga", "Finland") is not None


def test_accumulate_wave_delegates() -> None:
    manifest = EvalWaveManifest(
        wave_name="t",
        label="t",
        date_from="2026-06-18",
        date_to="2026-06-21",
        league_keys=("kazakhstan_premier",),
    )
    mock_acc = MagicMock(return_value={"fixtures_in_scope": 3, "persist_success": 2})
    runner = EvalWaveRunner(manifest=manifest, _accumulate_fn=mock_acc)
    out = runner.accumulate_wave()
    assert out["fixtures_in_scope"] == 3
    mock_acc.assert_called_once()
    assert mock_acc.call_args.kwargs["league_keys"] == ["kazakhstan_premier"]


def test_update_results_fail_soft() -> None:
    manifest = EvalWaveManifest(
        wave_name="t",
        label="t",
        date_from="2026-06-18",
        date_to="2026-06-21",
        league_keys=("kazakhstan_premier",),
    )

    def _boom(**_kwargs):
        raise RuntimeError("scraper down")

    runner = EvalWaveRunner(manifest=manifest, _update_results_fn=_boom)
    out = runner.update_results()
    assert out["status"] == "failed"
    assert out["results_saved"] == 0


def test_settle_wave_excludes_parked(tmp_path: Path) -> None:
    from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures

    db_path = tmp_path / "parked.db"
    fixtures = Path(__file__).parent / "data"
    run_v2_batch_persist_from_fixtures(
        fixtures,
        [{"flashscore_stem": "flashscore_kazakhstan_premier_match", "home_score": 1, "away_score": 0}],
        db_path=db_path,
        save_match_results=True,
    )
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE analysis_predictions_v2 SET prediction_json = json_set(prediction_json, '$.analysis_mode', 'analysis_only')"
    )
    conn.commit()
    conn.close()

    manifest = EvalWaveManifest(
        wave_name="t",
        label="t",
        date_from="2026-01-01",
        date_to="2026-12-31",
        league_keys=("kazakhstan_premier",),
    )
    runner = EvalWaveRunner(manifest=manifest, db_path=db_path)
    stl = runner.settle_wave()
    assert stl["parked_skipped"] == 1
    assert stl["settled_evaluable"] == 0


def test_report_wave_returns_calibration(tmp_path: Path) -> None:
    from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures

    db_path = tmp_path / "cal.db"
    fixtures = Path(__file__).parent / "data"
    run_v2_batch_persist_from_fixtures(
        fixtures,
        [{"flashscore_stem": "flashscore_kazakhstan_premier_match", "home_score": 2, "away_score": 1}],
        db_path=db_path,
        save_match_results=True,
    )
    manifest = EvalWaveManifest(
        wave_name="t",
        label="t",
        date_from="2026-01-01",
        date_to="2026-12-31",
        league_keys=("kazakhstan_premier",),
    )
    runner = EvalWaveRunner(manifest=manifest, db_path=db_path, output_dir=tmp_path / "out")
    report = runner.report_wave(write_artifacts=True)
    assert "calibration" in report
    assert report["calibration"]["sample"]["settled_evaluable_runs"] >= 1
    assert report["output_paths"]["json"]
    assert Path(report["output_paths"]["markdown"]).is_file()


def test_full_wave_partial_fail_soft() -> None:
    manifest = load_wave_manifest(preset="june18_21_first_batch")
    runner = EvalWaveRunner(
        manifest=manifest,
        _accumulate_fn=MagicMock(return_value={"fixtures_in_scope": 10, "persist_success": 8, "errors": []}),
        _update_results_fn=MagicMock(
            return_value={
                "results_saved": 2,
                "finished_in_scope": 2,
                "skipped_not_finished": 8,
            }
        ),
    )
    with patch.object(runner, "_build_reports") as mock_rep:
        mock_rep.return_value = (
            {"counts": {"pool_runs": 8}},
            {"sample": {"settled_evaluable_runs": 2, "sufficient_for_diagnostics": False}, "diagnostics": {"status": "insufficient_sample"}},
        )
        with patch.object(runner, "settle_wave") as mock_stl:
            mock_stl.return_value = {
                "settled_evaluable": 2,
                "unsettled": 6,
                "hit_rate": 0.5,
                "wins": 1,
                "losses": 1,
            }
            with tempfile.TemporaryDirectory() as tmp:
                runner.output_dir = Path(tmp)
                result = runner.full_wave(write_artifacts=True)
    assert result["stages"]["accumulate"]["fixtures_in_scope"] == 10
    assert result["output_paths"]["json"]
    assert "cli_summary" in result


def test_markdown_summary_generated() -> None:
    manifest = load_wave_manifest(preset="june18_21_first_batch")
    md = build_wave_markdown(
        manifest,
        {
            "accumulate": {"fixtures_in_scope": 41, "league_full_scored": 38, "persist_success": 38},
            "update_results": {"results_saved": 30, "skipped_not_finished": 11},
            "settlement": {"settled_evaluable": 30, "hit_rate": 0.55, "wins": 17, "losses": 13},
            "calibration": {
                "sample": {"settled_evaluable_runs": 30, "sufficient_for_diagnostics": True},
                "confidence_buckets": [{"confidence_bucket": "0.60-0.69", "count": 10, "hit_rate": 0.6, "avg_predicted_probability": 0.65, "roi_mean_profit": 0.02}],
                "market_buckets": [{"market_key": "HOME_NOT_LOSE", "count": 12, "hit_rate": 0.58, "avg_confidence": 0.62}],
                "league_buckets": [{"pool_key": "kazakhstan_premier", "count": 8, "hit_rate": 0.5, "odds_coverage_share": 0.75, "low_confidence_share": 0.1}],
                "diagnostics": {"status": "ok", "findings": []},
            },
        },
    )
    assert "Eval wave report" in md
    assert "41" in md
    assert "Confidence buckets" in md
