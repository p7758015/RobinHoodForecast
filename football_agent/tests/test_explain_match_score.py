"""Regression: strong home favorite should not pick underdog AWAY_NOT_LOSE (AC Oulu case)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2

_OULU_SNAPSHOT = Path(__file__).resolve().parents[1] / "debug" / "_oulu_snapshot.json"


@pytest.mark.skipif(not _OULU_SNAPSHOT.is_file(), reason="AC Oulu debug snapshot not present")
def test_ac_oulu_rescore_prefers_home_side_market() -> None:
    snap = MatchAnalysisSnapshotV2.model_validate(
        json.loads(_OULU_SNAPSHOT.read_text(encoding="utf-8"))
    )
    result = LeagueScorerV2().score_snapshot(snap)
    by_key = {m.market_key: m for m in result.market_predictions}
    assert by_key["HOME_NOT_LOSE"].probability > by_key["AWAY_NOT_LOSE"].probability
    assert result.best_market.market_key in ("HOME_WIN", "HOME_NOT_LOSE", "HOME_TEAM_TO_SCORE")


def test_dc_strength_alignment_penalizes_underdog_dc() -> None:
    from football_agent.domain.models_v2 import MarketPredictionV2
    from football_agent.scorers.league_scorer_v2 import _dc_strength_alignment_factor

    by_key = {
        "HOME_NOT_LOSE": MarketPredictionV2(market_key="HOME_NOT_LOSE", probability=0.80),
        "AWAY_NOT_LOSE": MarketPredictionV2(market_key="AWAY_NOT_LOSE", probability=0.52),
    }
    factor = _dc_strength_alignment_factor(
        by_key["AWAY_NOT_LOSE"],
        by_key=by_key,
        r_home=0.66,
        r_away=0.39,
    )
    assert factor < 0.55
