"""Scorer inspection helper tests."""

from __future__ import annotations

from football_agent.debug.scorer_inspection import build_scorer_inspection
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.tests.test_scorer_v2 import make_snapshot


def test_build_scorer_inspection_includes_weights_and_contributions() -> None:
    snap = make_snapshot(with_odds=True, season_phase=None)
    scored = ScoringServiceV2().score_snapshot_with_report(snap, BuildReport())
    insp = build_scorer_inspection(scored)
    assert "home" in insp
    assert "effective_weights" in insp["home"]
    assert "weighted_contributions" in insp["home"]
    assert insp["home"]["season_phase"] in ("EARLY", "MID", "LATE", "FINAL_RUN_IN")
    assert insp["best_market"] is not None
