"""
Calibration-driven selection policy (post-scorer layer).

Does NOT change LeagueScorerV2 probability/factor math — only:
- best_market tie-break / soft penalties (e.g. HOME_NOT_LOSE),
- express_safety downgrade from league noise + overconfidence guards,
- express candidate gates for ExpressBuilderV2.

Tunable via config env vars and league_registry metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from football_agent import config
from football_agent.domain.enums_v2 import ExpressSafetyClass, LeagueMarketKey
from football_agent.domain.models_v2 import (
    ExpressScreeningV2,
    MatchAnalysisSnapshotV2,
    MatchPredictionResultV2,
)
from football_agent.league_registry import get_league_config
from football_agent.scorers.league_scorer_v2 import HOME_ADVANTAGE, explain_best_market_scores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market selection penalties (multiplicative on pick_score)
# ---------------------------------------------------------------------------

MARKET_SCORE_PENALTY: Dict[str, float] = {
    LeagueMarketKey.HOME_NOT_LOSE.value: config.HOME_NOT_LOSE_MARKET_PENALTY,
}

# When top-2 policy scores are within this relative gap, prefer non-HOME_NOT_LOSE.
MARKET_CLOSE_SCORE_GAP = config.MARKET_CLOSE_SCORE_GAP

# Extra probability bar for HOME_NOT_LOSE in express assembly (on top of EXPRESS_MIN_PROBABILITY).
HOME_NOT_LOSE_EXPRESS_PROB_BONUS = config.HOME_NOT_LOSE_EXPRESS_PROB_BONUS

_REGISTRY_CODE_ALIASES: Dict[str, str] = {
    "FS_VIRSLIGA": "FS_LATVIA_VIRSLIGA",
    "FS_SERIE_B": "FS_BRAZIL_SERIE_B",
    "FS_MEISTRILIIGA": "FS_ESTONIA_MEISTRILIIGA",
    "FS_MEISTRILIIGA_WOMEN": "FS_ESTONIA_PREMIUM_LIIGA",
    "FS_CHINA_SUPER": "FS_CHINA_SUPER_LEAGUE",
    "FS_IRELAND_PREMIER": "FS_IRELAND_PREMIER",
}


def _normalize_registry_code(competition_code: str) -> str:
    code = (competition_code or "").strip().upper()
    return _REGISTRY_CODE_ALIASES.get(code, code)


def market_score_penalty(market_key: str) -> float:
    return float(MARKET_SCORE_PENALTY.get(market_key, 0.0))


def adjust_market_pick_score(raw_score: float, market_key: str) -> float:
    penalty = market_score_penalty(market_key)
    if penalty <= 0.0:
        return raw_score
    return raw_score * (1.0 - penalty)


def league_express_metadata(competition_code: str) -> Tuple[float, bool, str]:
    """
    Return (express_league_penalty, express_avoid, express_reliability).

    ``express_league_penalty`` is added to express screening penalty_score (0..1).
    """
    code = _normalize_registry_code(competition_code)
    cfg = get_league_config(code)
    if cfg is None:
        return 0.0, False, "UNKNOWN"
    penalty = float(getattr(cfg, "express_league_penalty", 0.0) or 0.0)
    avoid = bool(getattr(cfg, "express_avoid", False))
    reliability = str(getattr(cfg, "express_reliability", "NORMAL") or "NORMAL")
    return penalty, avoid, reliability


def _rank_markets_with_policy(
    prediction: MatchPredictionResultV2,
) -> List[Dict[str, Any]]:
    r_home = prediction.home_scoring.factor_scores.total_score + HOME_ADVANTAGE
    r_away = prediction.away_scoring.factor_scores.total_score
    conf = prediction.overall_confidence_score
    rows = explain_best_market_scores(
        prediction.market_predictions,
        conf,
        r_home=r_home,
        r_away=r_away,
    )
    for row in rows:
        raw = float(row["pick_score"])
        key = str(row["market_key"])
        row["pick_score_raw"] = raw
        row["pick_score_policy"] = round(adjust_market_pick_score(raw, key), 4)
    rows.sort(key=lambda r: r["pick_score_policy"], reverse=True)
    return rows


def _resolve_best_market_key(rows: List[Dict[str, Any]]) -> Optional[str]:
    if not rows:
        return None
    top = rows[0]
    winner_key = str(top["market_key"])
    if winner_key != LeagueMarketKey.HOME_NOT_LOSE.value or len(rows) < 2:
        return winner_key
    runner = rows[1]
    top_score = float(top["pick_score_policy"])
    runner_score = float(runner["pick_score_policy"])
    if top_score <= 0:
        return winner_key
    gap = (top_score - runner_score) / top_score
    if gap < MARKET_CLOSE_SCORE_GAP:
        logger.info(
            "selection_policy: HOME_NOT_LOSE tie-break -> %s (gap=%.3f < %.3f); "
            "HNL raw=%.4f policy=%.4f vs %s raw=%.4f policy=%.4f",
            runner["market_key"],
            gap,
            MARKET_CLOSE_SCORE_GAP,
            top.get("pick_score_raw"),
            top_score,
            runner["market_key"],
            runner.get("pick_score_raw"),
            runner_score,
        )
        return str(runner["market_key"])
    return winner_key


def apply_market_selection_policy(
    prediction: MatchPredictionResultV2,
) -> MatchPredictionResultV2:
    """Re-pick best_market using scorer pick_score + calibration penalties."""
    if not prediction.market_predictions:
        return prediction

    rows = _rank_markets_with_policy(prediction)
    winner_key = _resolve_best_market_key(rows)
    if not winner_key:
        return prediction

    current_key = prediction.best_market.market_key if prediction.best_market else None
    if winner_key == current_key:
        if current_key == LeagueMarketKey.HOME_NOT_LOSE.value and rows:
            top = rows[0]
            logger.debug(
                "selection_policy: best_market HOME_NOT_LOSE kept score_raw=%.4f score_policy=%.4f",
                top.get("pick_score_raw"),
                top.get("pick_score_policy"),
            )
        return prediction

    new_best = next(
        (m for m in prediction.market_predictions if m.market_key == winner_key),
        prediction.best_market,
    )
    old_best = prediction.best_market
    logger.info(
        "selection_policy: best_market %s -> %s (calibration market penalty)",
        current_key,
        winner_key,
    )
    if old_best and old_best.market_key == LeagueMarketKey.HOME_NOT_LOSE.value:
        competitors = [r for r in rows[:4] if r["market_key"] != LeagueMarketKey.HOME_NOT_LOSE.value]
        logger.info(
            "selection_policy: HOME_NOT_LOSE demoted raw=%.4f policy=%.4f; "
            "top_alternatives=%s",
            rows[0].get("pick_score_raw") if rows else None,
            rows[0].get("pick_score_policy") if rows else None,
            [(c["market_key"], c.get("pick_score_policy")) for c in competitors],
        )

    summary = prediction.prediction_summary or ""
    if new_best and "best " in summary:
        summary = summary.rsplit("best ", 1)[0] + f"best {new_best.market_key} p={new_best.probability:.0%}"

    return prediction.model_copy(update={"best_market": new_best, "prediction_summary": summary})


def apply_express_selection_policy(
    prediction: MatchPredictionResultV2,
    snapshot: MatchAnalysisSnapshotV2,
) -> MatchPredictionResultV2:
    """Downgrade / block express based on league noise and overconfidence guards."""
    express = prediction.express_safety
    if express is None:
        return prediction

    code = snapshot.match_meta.competition_code
    league_penalty, express_avoid, reliability = league_express_metadata(code)
    penalty = float(express.penalty_score)
    reasons = list(express.reasons or [])
    safety = express.safety_class
    allow = express.allow_for_express

    if express_avoid or league_penalty >= 0.40:
        safety = ExpressSafetyClass.EXPRESS_AVOID
        allow = False
        tag = f"league_express_avoid:{_normalize_registry_code(code)}"
        if tag not in reasons:
            reasons.append(tag)
        logger.info(
            "selection_policy: express AVOID match=%s league=%s reliability=%s penalty=%.2f",
            snapshot.match_meta.match_id,
            code,
            reliability,
            league_penalty,
        )
    elif league_penalty > 0.0:
        penalty = min(1.0, penalty + league_penalty)
        reasons.append(f"league_express_penalty:{_normalize_registry_code(code)}:{league_penalty:.2f}")
        if penalty >= 0.45:
            safety = ExpressSafetyClass.EXPRESS_AVOID
            allow = False
        elif penalty >= 0.22 and safety == ExpressSafetyClass.EXPRESS_SAFE:
            safety = ExpressSafetyClass.EXPRESS_CAUTION
            allow = allow and prediction.overall_confidence_score >= config.EXPRESS_SELECTION_CONFIDENCE_MIN

    best = prediction.best_market
    if best is not None and best.probability > config.EXPRESS_OVERCONFIDENCE_PROB_CAP:
        if safety == ExpressSafetyClass.EXPRESS_SAFE:
            safety = ExpressSafetyClass.EXPRESS_CAUTION
            reasons.append(
                f"overconfidence_prob_cap:{best.probability:.2f}>{config.EXPRESS_OVERCONFIDENCE_PROB_CAP:.2f}",
            )
            logger.debug(
                "selection_policy: downgrade EXPRESS_SAFE->CAUTION match=%s p=%.3f",
                snapshot.match_meta.match_id,
                best.probability,
            )

    conf_min = config.EXPRESS_SELECTION_CONFIDENCE_MIN
    if prediction.overall_confidence_score < conf_min:
        if allow:
            allow = False
            reasons.append(f"express_confidence_below_min:{prediction.overall_confidence_score:.3f}<{conf_min:.3f}")
        if safety == ExpressSafetyClass.EXPRESS_SAFE:
            safety = ExpressSafetyClass.EXPRESS_CAUTION

    new_express = express.model_copy(
        update={
            "penalty_score": min(1.0, penalty),
            "reasons": reasons,
            "safety_class": safety,
            "allow_for_express": allow,
        },
    )
    return prediction.model_copy(update={"express_safety": new_express})


def apply_calibration_selection_policy(
    prediction: MatchPredictionResultV2,
    snapshot: MatchAnalysisSnapshotV2,
) -> MatchPredictionResultV2:
    """Full post-scorer calibration selection pass."""
    updated = apply_market_selection_policy(prediction)
    return apply_express_selection_policy(updated, snapshot)


def express_candidate_allowed(result: MatchPredictionResultV2) -> Tuple[bool, Optional[str]]:
    """
    Extra gates for ExpressBuilderV2 (after scorer + express_selection_policy).

    Returns (allowed, skip_reason).
    """
    market = result.best_market
    if market is None:
        return False, "no_best_market"

    code = result.match_meta.competition_code
    league_penalty, express_avoid, _rel = league_express_metadata(code)
    if express_avoid:
        return False, f"league_express_avoid:{_normalize_registry_code(code)}"

    min_prob = config.EXPRESS_MIN_PROBABILITY
    if market.market_key == LeagueMarketKey.HOME_NOT_LOSE.value:
        min_prob += HOME_NOT_LOSE_EXPRESS_PROB_BONUS

    if market.probability < min_prob:
        return False, f"prob_below_min:{market.probability:.3f}<{min_prob:.3f}"

    if result.overall_confidence_score < config.EXPRESS_SELECTION_CONFIDENCE_MIN:
        return False, f"confidence_below_min:{result.overall_confidence_score:.3f}"

    if league_penalty >= 0.25 and result.express_safety.safety_class == ExpressSafetyClass.EXPRESS_SAFE:
        return False, f"league_penalty_blocks_safe:{league_penalty:.2f}"

    return True, None
