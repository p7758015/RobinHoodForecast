"""Tests for read-only wave prediction views."""

from __future__ import annotations

from pathlib import Path

from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.eval_pool.wave_predictions import (
    collect_wave_predictions,
    format_predictions_table,
    get_wave_prediction_by_run_id,
)
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures


def test_collect_wave_predictions(tmp_path: Path) -> None:
    db_path = tmp_path / "pred.db"
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
    views = collect_wave_predictions(manifest, db_path=db_path)
    assert len(views) == 1
    assert views[0].pool_key == "kazakhstan_premier"
    assert views[0].settle_status == "settled"
    assert views[0].final_score == "2-1"
    table = format_predictions_table(views)
    assert "kazakhstan_premier" in table
    detail = get_wave_prediction_by_run_id(views[0].run_id, db_path=db_path)
    assert detail is not None
    assert detail.run_id == views[0].run_id
