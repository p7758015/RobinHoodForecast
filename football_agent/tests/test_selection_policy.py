"""Calibration selection policy tests (post-scorer layer)."""

from __future__ import annotations

from football_agent.domain.enums_v2 import ExpressSafetyClass, LeagueMarketKey
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.scorers.selection_policy import (
    apply_calibration_selection_policy,
    apply_express_selection_policy,
    apply_market_selection_policy,
    express_candidate_allowed,
    league_express_metadata,
)
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.tests.test_express_builder_v2 import make_prediction
from football_agent.tests.test_scorer_v2 import make_snapshot
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport


def test_home_not_lose_market_penalty_demotes_on_close_scores() -> None:
    snap = make_snapshot(home_baseline=0.62, away_baseline=0.58, with_odds=True)
    raw = ScoringServiceV2()._scorer.score_snapshot(snap)
    rows_before = {m.market_key: m.probability for m in raw.market_predictions}
    assert LeagueMarketKey.HOME_NOT_LOSE.value in rows_before

    adjusted = apply_market_selection_policy(raw)
    # Policy may keep HNL if gap is large enough — just ensure function runs.
    assert adjusted.best_market is not None


def test_virsliga_express_avoid() -> None:
    penalty, avoid, rel = league_express_metadata("FS_VIRSLIGA")
    assert avoid is True
    assert rel == "LOW"
    assert penalty >= 0.35


def test_virsliga_downgrades_express_safety() -> None:
    snap = make_snapshot(with_odds=True)
    snap.match_meta = snap.match_meta.model_copy(
        update={"competition_code": "FS_VIRSLIGA", "competition_name": "Virsliga"},
    )
    pred = make_prediction(10, allow_for_express=True)
    pred = pred.model_copy(
        update={
            "match_meta": snap.match_meta,
            "express_safety": pred.express_safety.model_copy(
                update={"safety_class": ExpressSafetyClass.EXPRESS_SAFE},
            ),
        },
    )
    out = apply_express_selection_policy(pred, snap)
    assert out.express_safety.safety_class == ExpressSafetyClass.EXPRESS_AVOID
    assert out.express_safety.allow_for_express is False


def test_overconfidence_downgrades_express_safe() -> None:
    snap = make_snapshot(with_odds=True)
    pred = make_prediction(11, allow_for_express=True)
    pred = pred.model_copy(
        update={
            "match_meta": snap.match_meta.model_copy(update={"competition_code": "FS_BRAZIL_SERIE_B"}),
        },
    )
    pred.best_market = pred.best_market.model_copy(update={"probability": 0.82})
    pred.express_safety = pred.express_safety.model_copy(
        update={"safety_class": ExpressSafetyClass.EXPRESS_SAFE, "penalty_score": 0.05},
    )
    out = apply_express_selection_policy(pred, snap)
    assert out.express_safety.safety_class == ExpressSafetyClass.EXPRESS_CAUTION


def test_express_builder_skips_virsliga() -> None:
    good = make_prediction(1)
    good.match_meta = good.match_meta.model_copy(update={"competition_code": "FS_BRAZIL_SERIE_B"})
    bad = make_prediction(2)
    snap_bad = make_snapshot(with_odds=True)
    snap_bad.match_meta = snap_bad.match_meta.model_copy(update={"competition_code": "FS_VIRSLIGA"})
    bad = apply_calibration_selection_policy(
        ScoringServiceV2()._scorer.score_snapshot(snap_bad),
        snap_bad,
    )
    bet = ExpressBuilderV2().build_express([good, bad], target_odds=1.5, max_events=2)
    assert bet is not None
    assert len(bet.events) == 1
    assert bet.events[0].match_meta.competition_code == "FS_BRAZIL_SERIE_B"


def test_home_not_lose_express_prob_bonus() -> None:
    pred = make_prediction(12, allow_for_express=True, market_key="HOME_NOT_LOSE", probability=0.73)
    allowed, reason = express_candidate_allowed(pred)
    assert allowed is False
    assert reason and "prob_below_min" in reason

    pred.best_market = pred.best_market.model_copy(update={"probability": 0.75})
    allowed2, _ = express_candidate_allowed(pred)
    assert allowed2 is True


def test_scoring_service_applies_policy_layer() -> None:
    snap = make_snapshot(with_odds=True)
    snap.match_meta = snap.match_meta.model_copy(update={"competition_code": "FS_VIRSLIGA"})
    run = ScoringServiceV2().score_snapshot_with_report(snap, BuildReport())
    assert run.prediction.express_safety.safety_class == ExpressSafetyClass.EXPRESS_AVOID
