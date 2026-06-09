"""Branch selection for v1 vs v2 pipeline (no API)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from football_agent import app_pipeline
from football_agent.app_pipeline import handle_request


def _req(req_type: str = "all_matches") -> dict:
    return {"type": req_type, "date": "2024-04-25", "target_odds": 3.0}


@patch("football_agent.app_pipeline.USE_V2_PIPELINE", False)
@patch("football_agent.app_pipeline._run_v1")
def test_flag_off_calls_v1(mock_v1: MagicMock) -> None:
    mock_v1.return_value = "v1-ok"
    fd, af, db = MagicMock(), MagicMock(), MagicMock()
    out = handle_request(_req(), fd, af, db)
    assert out == "v1-ok"
    mock_v1.assert_called_once()


@patch("football_agent.app_pipeline.USE_V2_PIPELINE", True)
@patch("football_agent.app_pipeline._run_v2")
def test_flag_on_calls_v2(mock_v2: MagicMock) -> None:
    mock_v2.return_value = "v2-ok"
    fd, af, db = MagicMock(), MagicMock(), MagicMock()
    out = handle_request(_req("express"), fd, af, db)
    assert out == "v2-ok"
    mock_v2.assert_called_once()


def test_pipeline_label_reflects_flag() -> None:
    with patch.object(app_pipeline, "USE_V2_PIPELINE", False):
        assert app_pipeline.pipeline_label() == "v1"
    with patch.object(app_pipeline, "USE_V2_PIPELINE", True):
        assert app_pipeline.pipeline_label() == "v2"
