"""FormCollector tests."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.flashscore.form_collector import FormCollector
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw

_FIXTURES = Path(__file__).resolve().parents[1] / "data"


def _raw(name: str) -> dict:
    data = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return enrich_http_flashscore_raw(data)


def test_form_partial_from_empty_lists() -> None:
    raw = _raw("flashscore_botola_sample_match.json")
    result = FormCollector().collect(raw, MatchRef())
    assert result.status == "missing"
    assert "form_no_signal" in result.warnings


def test_form_ok_with_five_results() -> None:
    raw = _raw("flashscore_sample_league_match.json")
    raw["form"] = {
        "home": {"last_n_results": ["W", "W", "D", "L", "W"]},
        "away": {"last_n_results": ["L", "W", "W", "D", "W"]},
    }
    result = FormCollector().collect(raw, MatchRef())
    assert result.status == "ok"
    assert result.confidence >= 0.7
    assert len(result.payload["home"]["last_n_results"]) == 5
