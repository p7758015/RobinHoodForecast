"""
LeagueScorerV2: MatchAnalysisSnapshotV2 → MatchPredictionResultV2.

Uses v1 market probability math where applicable (probability_model.compute_market_probabilities).
All inputs come from the v2 snapshot contract only — no API calls.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_agent.config import BEST_MARKET_MIN_USEFUL_ODDS
from football_agent.domain.enums_v2 import ExpressSafetyClass, LeagueMarketKey
from football_agent.domain.models import H2HStats
from football_agent.domain.models_v2 import (
    CoachContextV2,
    ExpressScreeningV2,
    H2HContextV2,
    LeagueFactorScoresV2,
    MarketPredictionV2,
    MatchAnalysisSnapshotV2,
    MatchPredictionResultV2,
    OddsContextV2,
    ScheduleContextV2,
    SquadContextV2,
    TeamContextV2,
    TeamScoringResultV2,
)
from football_agent.domain.probability_model import compute_market_probabilities
from football_agent.scorers.league_factor_weights import (
    ResolvedWeights,
    resolve_season_phase,
    resolve_weights_from_snapshot,
)

logger = logging.getLogger(__name__)

HOME_ADVANTAGE = 0.04

MARKET_KEYS: Tuple[str, ...] = tuple(m.value for m in LeagueMarketKey)

MARKET_LABELS: Dict[str, str] = {
    "HOME_WIN": "П1",
    "AWAY_WIN": "П2",
    "HOME_NOT_LOSE": "1X (хозяева не проиграют)",
    "AWAY_NOT_LOSE": "X2 (гости не проиграют)",
    "BTTS_YES": "Обе забьют — Да",
    "HOME_TEAM_TO_SCORE": "Команда 1 забьёт — Да",
    "AWAY_TEAM_TO_SCORE": "Команда 2 забьёт — Да",
    "OVER_1_5": "Тотал больше 1.5",
}

RESULT_MARKET_KEYS = frozenset(
    {"HOME_WIN", "AWAY_WIN", "HOME_NOT_LOSE", "AWAY_NOT_LOSE"}
)

DOUBLE_CHANCE_KEYS = frozenset({"HOME_NOT_LOSE", "AWAY_NOT_LOSE"})

# BTTS excluded from best_market (high p + edge skew); other extended markets allowed.
BEST_MARKET_CANDIDATE_KEYS = RESULT_MARKET_KEYS | frozenset(
    {"HOME_TEAM_TO_SCORE", "AWAY_TEAM_TO_SCORE", "OVER_1_5"}
)

BEST_MARKET_TYPE_WEIGHT: Dict[str, float] = {
    "HOME_WIN": 1.0,
    "AWAY_WIN": 1.0,
    "HOME_NOT_LOSE": 0.86,
    "AWAY_NOT_LOSE": 0.86,
    "HOME_TEAM_TO_SCORE": 0.90,
    "AWAY_TEAM_TO_SCORE": 0.90,
    "BTTS_YES": 0.76,
    "OVER_1_5": 0.80,
}


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _prob_to_fair_odds(p: float) -> Optional[float]:
    if p <= 0.01:
        return None
    return round(1.0 / p, 2)


def _edge(probability: float, book_odds: Optional[float]) -> Optional[float]:
    if book_odds is None or book_odds <= 1.0:
        return None
    implied = 1.0 / book_odds
    return round(probability - implied, 4)


@dataclass(frozen=True)
class _TeamInputs:
    context: TeamContextV2
    squad: SquadContextV2
    coach: CoachContextV2
    schedule: ScheduleContextV2
    h2h_bias: float  # signed adjustment for this side
    resolved_weights: ResolvedWeights


class LeagueScorerV2:
    """Snapshot in → MatchPredictionResultV2 out."""

    def score_snapshot(self, snapshot: MatchAnalysisSnapshotV2) -> MatchPredictionResultV2:
        conf = snapshot.confidence.overall_confidence_score
        season_g = snapshot.match_meta.season_progress
        phase = resolve_season_phase(
            season_phase=snapshot.match_meta.season_phase,
            season_progress=float(season_g or 0.0),
        )

        home_weights = resolve_weights_from_snapshot(snapshot, side="home")
        away_weights = resolve_weights_from_snapshot(snapshot, side="away")

        home_in = _TeamInputs(
            context=snapshot.home_team_context,
            squad=snapshot.home_squad,
            coach=snapshot.home_coach,
            schedule=snapshot.home_schedule,
            h2h_bias=snapshot.h2h_context.h2h_context_bias,
            resolved_weights=home_weights,
        )
        away_in = _TeamInputs(
            context=snapshot.away_team_context,
            squad=snapshot.away_squad,
            coach=snapshot.away_coach,
            schedule=snapshot.away_schedule,
            h2h_bias=-snapshot.h2h_context.h2h_context_bias,
            resolved_weights=away_weights,
        )

        home_scoring = self._score_team(home_in, phase, conf, is_home=True)
        away_scoring = self._score_team(away_in, phase, conf, is_home=False)

        r_home = home_scoring.factor_scores.total_score + HOME_ADVANTAGE
        r_away = away_scoring.factor_scores.total_score

        h2h_v1 = _h2h_stats_from_context(snapshot.h2h_context)
        avg_gf_home, avg_gf_away = _estimate_avg_goals(snapshot)

        probs = compute_market_probabilities(
            R_home=r_home,
            R_away=r_away,
            avg_goals_home=avg_gf_home,
            avg_goals_away=avg_gf_away,
            h2h=h2h_v1,
        )
        probs = _extend_market_probabilities(
            probs,
            r_home=r_home,
            r_away=r_away,
            avg_goals_home=avg_gf_home,
            avg_goals_away=avg_gf_away,
            h2h_btts=snapshot.h2h_context.h2h_btts_rate,
            over25_rate=snapshot.h2h_context.h2h_over25_rate,
        )
        probs = _confidence_calibrate_probabilities(probs, conf)

        book_odds = _book_odds_map(snapshot.odds)
        markets = self._build_market_predictions(probs, book_odds, conf)
        best = self._pick_best_market(markets, conf)

        balance = abs(r_home - r_away)
        peak_result_prob = max(
            (m.probability for m in markets if m.market_key in RESULT_MARKET_KEYS),
            default=best.probability,
        )
        express = self._build_express_screening(snapshot, best, conf, balance, peak_result_prob)

        summary = (
            f"{snapshot.match_meta.home_team.short_name or snapshot.match_meta.home_team.name} vs "
            f"{snapshot.match_meta.away_team.short_name or snapshot.match_meta.away_team.name}: "
            f"best {best.market_key} p={best.probability:.0%}"
        )

        return MatchPredictionResultV2(
            match_meta=snapshot.match_meta,
            home_scoring=home_scoring,
            away_scoring=away_scoring,
            market_predictions=markets,
            best_market=best,
            express_safety=express,
            prediction_summary=summary,
            overall_confidence_score=_clip01(conf),
        )

    def score_many(self, snapshots: List[MatchAnalysisSnapshotV2]) -> List[MatchPredictionResultV2]:
        results: List[MatchPredictionResultV2] = []
        for snap in snapshots:
            try:
                results.append(self.score_snapshot(snap))
            except Exception as e:
                logger.warning(
                    "Scoring failed for match %s: %s",
                    snap.match_meta.match_id,
                    e,
                )
        return results

    # ------------------------------------------------------------------
    # Team scoring
    # ------------------------------------------------------------------

    def _score_team(
        self,
        inp: _TeamInputs,
        season_phase,
        overall_confidence: float,
        is_home: bool,
    ) -> TeamScoringResultV2:
        ctx = inp.context
        factors = LeagueFactorScoresV2(
            baseline_strength=_clip01(ctx.baseline_strength_score),
            current_form=self._form_score(ctx, inp.coach),
            motivation=_clip01(ctx.motivation.motivation_score),
            squad_availability=self._squad_score(inp.squad, overall_confidence),
            coach_factor=self._coach_score(inp.coach),
            schedule_context=self._schedule_score(inp.schedule),
            h2h_context_bias=max(-0.3, min(0.3, inp.h2h_bias)),
        )
        if inp.coach.is_first_match:
            factors.motivation = _clip01(factors.motivation + 0.05)

        factors.total_score = self._compute_total_score(
            factors,
            inp.resolved_weights,
            inp,
            overall_confidence,
        )

        flags = self._make_summary_flags(inp, factors, overall_confidence, is_home)
        if inp.resolved_weights.special_overrides_applied:
            for tag in inp.resolved_weights.special_overrides_applied:
                flag = {
                    "new_coach_first_match": "new_coach",
                    "new_coach_window": "coach_bounce_window",
                    "pre_big_match": "pre_big_match_risk",
                    "post_big_match": "post_big_match_fatigue",
                }.get(tag)
                if flag and flag not in flags:
                    flags.append(flag)
        return TeamScoringResultV2(
            team=ctx.team,
            factor_scores=factors,
            summary_flags=flags,
        )

    @staticmethod
    def _form_score(ctx: TeamContextV2, coach: CoachContextV2) -> float:
        f = ctx.form
        venue_avg = (f.home_form_score + f.away_form_score) / 2.0
        if coach.is_first_match:
            return _clip01(
                0.30 * f.last_5_form_score
                + 0.15 * f.last_10_form_score
                + 0.40 * f.form_under_current_coach
                + 0.15 * venue_avg
            )
        if coach.is_new_coach_bounce_window or (
            coach.matches_in_charge is not None and 2 <= coach.matches_in_charge <= 4
        ):
            return _clip01(
                0.35 * f.last_5_form_score
                + 0.15 * f.last_10_form_score
                + 0.35 * f.form_under_current_coach
                + 0.15 * venue_avg
            )
        return _clip01(
            0.45 * f.last_5_form_score
            + 0.25 * f.last_10_form_score
            + 0.20 * f.form_under_current_coach
            + 0.10 * venue_avg
        )

    @staticmethod
    def _squad_score(squad: SquadContextV2, confidence: float) -> float:
        base = squad.starting_xi_confidence
        if squad.missing_key_players_count > 0:
            base -= 0.08 * min(squad.missing_key_players_count, 4)
        if squad.missing_players_count > 3:
            base -= 0.05
        # Unknown squad data → conservative, not fake-strong
        if base <= 0.25 and squad.starting_xi_confidence <= 0.25:
            return _clip01(0.45 * confidence + 0.25)
        return _clip01(base)

    @staticmethod
    def _coach_score(coach: CoachContextV2) -> float:
        base = (
            0.45 * coach.coach_global_strength_score
            + 0.35 * coach.coach_vs_opponent_team_score
            + 0.20 * coach.coach_vs_opponent_coach_score
        )
        if coach.is_first_match:
            base = min(1.0, base + 0.12)
        elif coach.is_new_coach_bounce_window or (
            coach.matches_in_charge is not None and 2 <= coach.matches_in_charge <= 4
        ):
            base = min(1.0, base + 0.06)
        return _clip01(base)

    @staticmethod
    def _schedule_score(schedule: ScheduleContextV2) -> float:
        risks = [
            schedule.rotation_risk_score,
            schedule.fixture_congestion_score,
            schedule.pre_big_match_preservation_risk,
            schedule.post_big_match_relaxation_risk,
            schedule.emotional_swing_score * 0.5,
        ]
        avg_risk = sum(risks) / len(risks)
        return _clip01(1.0 - avg_risk)

    def _compute_total_score(
        self,
        factors: LeagueFactorScoresV2,
        resolved: ResolvedWeights,
        inp: _TeamInputs,
        overall_confidence: float,
    ) -> float:
        """Blueprint weighted sum: phase weights + confidence gating + schedule penalties."""
        w = resolved.weights
        total = (
            w["baseline_strength"] * factors.baseline_strength
            + w["current_form"] * factors.current_form
            + w["motivation"] * factors.motivation
            + w["squad_availability"] * factors.squad_availability
            + w["coach_factor"] * factors.coach_factor
            + w["schedule_context"] * factors.schedule_context
            + w["h2h_context_bias"] * factors.h2h_context_bias
        )
        total -= self._schedule_special_penalty(inp)
        if inp.coach.is_first_match and inp.squad.starting_xi_confidence < 0.35:
            total -= 0.03 * (1.0 - overall_confidence)
        return _clip01(total)

    @staticmethod
    def _schedule_special_penalty(inp: _TeamInputs) -> float:
        sched = inp.schedule
        penalty = 0.0
        if sched.pre_big_match_preservation_risk >= 0.25:
            penalty += 0.04 + 0.10 * sched.pre_big_match_preservation_risk
            if sched.days_to_next_match is not None and sched.days_to_next_match <= 3:
                penalty += 0.03
        if sched.post_big_match_relaxation_risk >= 0.15:
            penalty += 0.03 + 0.08 * sched.post_big_match_relaxation_risk
            if sched.days_since_last_match is not None and sched.days_since_last_match <= 3:
                penalty += 0.03
        if sched.rotation_risk_score >= 0.55 and sched.pre_big_match_preservation_risk >= 0.2:
            penalty += 0.02
        return min(0.15, penalty)

    @staticmethod
    def _make_summary_flags(
        inp: _TeamInputs,
        factors: LeagueFactorScoresV2,
        confidence: float,
        is_home: bool,
    ) -> List[str]:
        flags: List[str] = []
        if factors.motivation >= 0.75:
            flags.append("high_motivation")
        if factors.current_form <= 0.4:
            flags.append("poor_form")
        if factors.current_form >= 0.7:
            flags.append("strong_form")
        if inp.coach.is_first_match:
            flags.append("new_coach")
        elif inp.coach.is_new_coach_bounce_window:
            flags.append("coach_bounce_window")
        if inp.squad.starting_xi_confidence < 0.35:
            flags.append("thin_squad")
        if factors.schedule_context <= 0.45 or (
            inp.schedule.rotation_risk_score >= 0.55
            or inp.schedule.fixture_congestion_score >= 0.55
        ):
            flags.append("schedule_risk")
        if inp.schedule.pre_big_match_preservation_risk >= 0.25:
            flags.append("pre_big_match_risk")
        if inp.schedule.post_big_match_relaxation_risk >= 0.15:
            flags.append("post_big_match_fatigue")
        if confidence < 0.45:
            flags.append("low_confidence_data")
        if is_home:
            flags.append("home_side")
        return flags

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def _build_market_predictions(
        self,
        probs: Dict[str, float],
        book_odds: Dict[str, Optional[float]],
        confidence: float,
    ) -> List[MarketPredictionV2]:
        markets: List[MarketPredictionV2] = []
        for key in MARKET_KEYS:
            p = float(probs.get(key, 0.0))
            bo = book_odds.get(key)
            fair = _prob_to_fair_odds(p)
            markets.append(
                MarketPredictionV2(
                    market_key=key,
                    probability=p,
                    fair_odds=fair,
                    book_odds=bo,
                    edge=_edge(p, bo),
                    label=MARKET_LABELS.get(key, key),
                )
            )
        markets.sort(key=lambda m: m.probability, reverse=True)
        return markets

    def _pick_best_market(
        self,
        markets: List[MarketPredictionV2],
        confidence: float,
    ) -> MarketPredictionV2:
        if not markets:
            return MarketPredictionV2(market_key="HOME_NOT_LOSE", probability=0.5, label="1X")

        by_key = {m.market_key: m for m in markets}

        def score(m: MarketPredictionV2) -> float:
            p = m.probability
            edge_bonus = max(0.0, (m.edge or 0.0)) * 2.0
            odds_factor = _best_market_odds_factor(m.book_odds)
            conf_factor = 0.55 + 0.45 * confidence
            type_weight = BEST_MARKET_TYPE_WEIGHT.get(m.market_key, 0.85)
            val = p * conf_factor * odds_factor * type_weight * (1.0 + edge_bonus)
            if m.market_key in DOUBLE_CHANCE_KEYS:
                if m.book_odds is None:
                    val *= 0.78
                elif m.book_odds < BEST_MARKET_MIN_USEFUL_ODDS:
                    val *= 0.86
                if p > 0.90:
                    win_key = "HOME_WIN" if m.market_key == "HOME_NOT_LOSE" else "AWAY_WIN"
                    win_m = by_key.get(win_key)
                    if win_m and win_m.probability >= 0.50:
                        val *= 0.90
            return val

        pool = [m for m in markets if m.market_key in BEST_MARKET_CANDIDATE_KEYS]
        if not pool:
            pool = [m for m in markets if m.market_key in RESULT_MARKET_KEYS] or list(markets)
        result_pool = [m for m in pool if m.market_key in RESULT_MARKET_KEYS]
        if not result_pool:
            return max(pool, key=score)
        best_result = max(result_pool, key=score)
        best_any = max(pool, key=score)
        winner = best_any if best_any.market_key not in RESULT_MARKET_KEYS and score(best_any) > score(best_result) * 1.10 else best_result

        if winner.market_key in DOUBLE_CHANCE_KEYS:
            for alt in sorted(pool, key=score, reverse=True):
                if alt.market_key in DOUBLE_CHANCE_KEYS:
                    continue
                if alt.book_odds is None or alt.book_odds < BEST_MARKET_MIN_USEFUL_ODDS:
                    continue
                if score(alt) >= score(winner) * 0.94:
                    return alt
        return winner

    # ------------------------------------------------------------------
    # Express screening
    # ------------------------------------------------------------------

    def _build_express_screening(
        self,
        snapshot: MatchAnalysisSnapshotV2,
        best: MarketPredictionV2,
        confidence: float,
        balance: float,
        peak_result_prob: float,
    ) -> ExpressScreeningV2:
        reasons: List[str] = []
        penalty = 0.0

        if confidence < 0.45:
            penalty += 0.25
            reasons.append("low_snapshot_confidence")
        if snapshot.home_squad.starting_xi_confidence < 0.35 or snapshot.away_squad.starting_xi_confidence < 0.35:
            penalty += 0.20
            reasons.append("uncertain_lineups")
        if snapshot.home_coach.is_first_match or snapshot.away_coach.is_first_match:
            penalty += 0.15
            reasons.append("new_coach_first_match")
        sched_risk = max(
            snapshot.home_schedule.rotation_risk_score,
            snapshot.away_schedule.rotation_risk_score,
            snapshot.home_schedule.fixture_congestion_score,
            snapshot.away_schedule.fixture_congestion_score,
        )
        if sched_risk >= 0.55:
            penalty += 0.15
            reasons.append("schedule_volatility")
        if snapshot.h2h_context.team_h2h_total_matches >= 3 and abs(snapshot.h2h_context.h2h_context_bias) > 0.2:
            penalty += 0.08
            reasons.append("strong_h2h_skew")
        if best.book_odds is None:
            penalty += 0.10
            reasons.append("no_book_odds")
        if best.edge is not None and best.edge < 0.02:
            penalty += 0.08
            reasons.append("weak_edge")
        if balance < 0.06:
            penalty += 0.12
            reasons.append("tight_match_balance")
        if peak_result_prob < 0.68:
            penalty += 0.10
            reasons.append("moderate_probability")

        penalty = _clip01(penalty)

        if penalty >= 0.45:
            safety = ExpressSafetyClass.EXPRESS_AVOID
            allow = False
        elif penalty >= 0.22:
            safety = ExpressSafetyClass.EXPRESS_CAUTION
            allow = peak_result_prob >= 0.72 and confidence >= 0.5
        else:
            safety = ExpressSafetyClass.EXPRESS_SAFE
            allow = peak_result_prob >= 0.68 and confidence >= 0.45

        return ExpressScreeningV2(
            safety_class=safety,
            penalty_score=penalty,
            reasons=reasons,
            allow_for_express=allow,
        )


# ---------------------------------------------------------------------------
# Helpers: snapshot → v1 probability inputs
# ---------------------------------------------------------------------------


def _confidence_calibrate_probabilities(probs: Dict[str, float], confidence: float) -> Dict[str, float]:
    """Shrink extreme probabilities toward 0.5 when snapshot confidence is low."""
    if confidence >= 0.65:
        return probs
    shrink = max(0.35, min(1.0, 0.35 + 0.65 * _clip01(confidence)))
    return {k: round(_clip01(0.5 + (float(p) - 0.5) * shrink), 4) for k, p in probs.items()}


def _best_market_odds_factor(book_odds: Optional[float]) -> float:
    if book_odds is None:
        return 0.75
    if book_odds < 1.20:
        return 0.45
    if book_odds < BEST_MARKET_MIN_USEFUL_ODDS:
        span = BEST_MARKET_MIN_USEFUL_ODDS - 1.20
        return 0.55 + 0.45 * (book_odds - 1.20) / span
    return 1.0


def _extend_market_probabilities(
    base: Dict[str, float],
    *,
    r_home: float,
    r_away: float,
    avg_goals_home: float,
    avg_goals_away: float,
    h2h_btts: float,
    over25_rate: float,
) -> Dict[str, float]:
    """Add team-to-score and OVER_1_5 heuristics consistent with base 1X2/BTTS."""
    probs = dict(base)
    p_home_win = probs.get("HOME_WIN", 0.5)
    p_away_win = probs.get("AWAY_WIN", 0.5)
    p_btts = probs.get("BTTS_YES", 0.5)

    lam = avg_goals_home + avg_goals_away
    p_home_score = _clip01(1.0 - math.exp(-max(0.4, avg_goals_home) * 0.9))
    p_away_score = _clip01(1.0 - math.exp(-max(0.4, avg_goals_away) * 0.9))
    p_home_score = max(p_home_score, p_home_win * 0.82 + 0.08)
    p_away_score = max(p_away_score, p_away_win * 0.82 + 0.06)
    p_home_score = min(p_home_score, 0.93)
    p_away_score = min(p_away_score, 0.88)
    if r_away < 0.42:
        p_away_score = min(p_away_score, 0.62)
    if r_home < 0.42:
        p_home_score = min(p_home_score, 0.62)

    p_over15 = _clip01(0.50 + (lam - 1.8) * 0.14 + over25_rate * 0.12)
    p_over15 = max(p_over15, p_btts + 0.04, min(p_home_score, p_away_score) * 0.5 + 0.35)
    p_over15 = min(p_over15, 0.92)

    probs["HOME_TEAM_TO_SCORE"] = round(p_home_score, 4)
    probs["AWAY_TEAM_TO_SCORE"] = round(p_away_score, 4)
    probs["OVER_1_5"] = round(p_over15, 4)
    return probs


def _h2h_stats_from_context(h2h: H2HContextV2) -> H2HStats:
    """Approximate v1 H2HStats from v2 context for probability_model reuse."""
    total = h2h.team_h2h_total_matches
    if total <= 0:
        return H2HStats(
            total_matches=0,
            home_wins=0,
            away_wins=0,
            draws=0,
            home_goals_avg=0.0,
            away_goals_avg=0.0,
            btts_rate=0.5,
            over25_rate=0.5,
        )

    draw_est = max(0, int(total * 0.22))
    decisive = max(1, total - draw_est)
    home_win_rate = _clip01(0.5 + h2h.team_h2h_recent_score / 2.0)
    home_wins = min(decisive, max(0, int(round(home_win_rate * decisive))))
    away_wins = max(0, decisive - home_wins)
    draws = max(0, total - home_wins - away_wins)

    return H2HStats(
        total_matches=total,
        home_wins=home_wins,
        away_wins=away_wins,
        draws=draws,
        home_goals_avg=max(0.5, h2h.h2h_btts_rate * 1.5),
        away_goals_avg=max(0.5, h2h.h2h_btts_rate * 1.5),
        btts_rate=h2h.h2h_btts_rate,
        over25_rate=h2h.h2h_over25_rate,
    )


def _estimate_avg_goals(snapshot: MatchAnalysisSnapshotV2) -> Tuple[float, float]:
    h2h = snapshot.h2h_context
    hf = snapshot.home_team_context.form.last_5_form_score
    af = snapshot.away_team_context.form.last_5_form_score

    if h2h.team_h2h_total_matches >= 2:
        home_g = max(0.6, min(2.8, 0.9 + h2h.h2h_btts_rate))
        away_g = max(0.6, min(2.8, 0.9 + h2h.h2h_btts_rate))
    else:
        home_g = max(0.7, min(2.5, 0.8 + hf * 1.2))
        away_g = max(0.7, min(2.5, 0.8 + af * 1.2))
    return home_g, away_g


def _book_odds_map(odds: OddsContextV2) -> Dict[str, Optional[float]]:
    def val(m) -> Optional[float]:
        return float(m.odds) if m is not None else None

    return {
        "HOME_WIN": val(odds.home_win),
        "AWAY_WIN": val(odds.away_win),
        "HOME_NOT_LOSE": val(odds.home_not_lose),
        "AWAY_NOT_LOSE": val(odds.away_not_lose),
        "BTTS_YES": val(odds.btts_yes),
        "HOME_TEAM_TO_SCORE": val(odds.home_team_to_score),
        "AWAY_TEAM_TO_SCORE": val(odds.away_team_to_score),
        "OVER_1_5": val(odds.over_15),
    }


if __name__ == "__main__":
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from football_agent.domain.models import Match, StandingEntry, Team
    from football_agent.normalizers.match_snapshot_builder import MatchSnapshotBuilder

    logging.basicConfig(level=logging.INFO)

    home = Team(id=1, name="Arsenal FC", short_name="Arsenal")
    away = Team(id=2, name="Chelsea FC", short_name="Chelsea")
    match = Match(
        id=99,
        competition_code="PL",
        home_team=home,
        away_team=away,
        utc_date=datetime(2024, 4, 25, 15, 0, tzinfo=timezone.utc),
        status="SCHEDULED",
        matchday=34,
    )
    standings = [
        StandingEntry(
            team=home,
            position=1,
            points=80,
            played_games=34,
            won=25,
            draw=5,
            lost=4,
            goals_for=70,
            goals_against=30,
            goal_difference=40,
            form="W,W,D,W,W",
        ),
        StandingEntry(
            team=away,
            position=4,
            points=60,
            played_games=34,
            won=17,
            draw=9,
            lost=8,
            goals_for=55,
            goals_against=40,
            goal_difference=15,
            form="D,W,L,W,D",
        ),
    ]
    fd = MagicMock()
    fd.get_standings.return_value = standings
    fd.get_team_matches_season.return_value = []
    fd.get_team_coach.return_value = (10, "Coach A", datetime(2023, 7, 1).date())
    fd.get_coach_matches.return_value = []
    fd.get_h2h_matches.return_value = []
    af = MagicMock()
    af.find_fixture_id.return_value = None

    snap = MatchSnapshotBuilder(fd, af).build_snapshot_for_match(match)
    pred = LeagueScorerV2().score_snapshot(snap)
    print("best:", pred.best_market.market_key, pred.best_market.probability)
    print("express:", pred.express_safety.safety_class, pred.express_safety.allow_for_express)
    print("home total:", pred.home_scoring.factor_scores.total_score)
    print("flags:", pred.home_scoring.summary_flags)
