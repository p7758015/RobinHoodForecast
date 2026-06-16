"""
Express / parlay candidate split (Phase Evaluation A).

Separates matches usable for coefficient-based parlay assembly from
prediction-only candidates when odds are missing.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field

from football_agent.domain.models_v2 import MatchPredictionResultV2, V2OutputModel
from football_agent.evaluation.market_key_map import scorer_market_to_coverage_key
from football_agent.odds.coverage import build_match_odds_coverage
from football_agent.odds.coverage_models import MatchOddsCoverage
from football_agent.odds.models import MatchOddsContext


class ParlayMarketCandidate(V2OutputModel):
    """One match leg candidate for express assembly or prediction-only shortlist."""

    match_id: Optional[int] = None
    match_key: Optional[str] = None
    home_team: str
    away_team: str
    competition_code: Optional[str] = None
    market_key: str
    probability: float = Field(ge=0.0, le=1.0)
    book_odds: Optional[float] = Field(default=None, gt=1.0)
    has_odds: bool = False
    derived_odds: bool = False
    suitable_for_pricing: bool = False
    recommended_by_model: bool = False
    note: Optional[str] = None


class ParlayCandidateSplit(V2OutputModel):
    """Backend-ready split for target-odds express vs prediction-only suggestions."""

    pricing_candidates: List[ParlayMarketCandidate] = Field(default_factory=list)
    no_odds_candidates: List[ParlayMarketCandidate] = Field(default_factory=list)


def _best_prediction_market(result: MatchPredictionResultV2):
    if result.best_market is not None:
        return result.best_market
    if not result.market_predictions:
        return None
    return max(result.market_predictions, key=lambda m: m.probability)


def _book_odds_for_market(
    result: MatchPredictionResultV2,
    market_key: str,
    coverage: Optional[MatchOddsCoverage],
) -> tuple[Optional[float], bool, bool]:
    """Return (odds_value, has_odds, derived)."""
    for mp in result.market_predictions:
        if mp.market_key == market_key and mp.book_odds is not None and mp.book_odds > 1.0:
            cov_key = scorer_market_to_coverage_key(market_key)
            derived = False
            if coverage and cov_key and cov_key in coverage.markets:
                entry = coverage.markets[cov_key]
                derived = entry.derived
            return float(mp.book_odds), True, derived

    cov_key = scorer_market_to_coverage_key(market_key)
    if coverage and cov_key and cov_key in coverage.markets:
        entry = coverage.markets[cov_key]
        if entry.has_odds and entry.odds_value is not None:
            return entry.odds_value, True, entry.derived
    return None, False, False


def split_parlay_candidates(
    results: List[MatchPredictionResultV2],
    *,
    min_probability: float = 0.72,
    include_derived_pricing: bool = True,
    odds_context_by_match_id: Optional[Dict[str, MatchOddsContext]] = None,
) -> ParlayCandidateSplit:
    """
    Split scorer outputs into pricing vs no-odds candidate lists.

    pricing_candidates: suitable_for_pricing=True (book or optionally derived odds).
    no_odds_candidates: high model probability but no usable odds — note odds_unavailable.
    """
    odds_context_by_match_id = odds_context_by_match_id or {}
    pricing: List[ParlayMarketCandidate] = []
    no_odds: List[ParlayMarketCandidate] = []

    for result in results:
        market = _best_prediction_market(result)
        if market is None or market.probability < min_probability:
            continue

        meta = result.match_meta
        mid_str = str(meta.match_id) if meta.match_id is not None else ""
        odds_ctx = odds_context_by_match_id.get(mid_str)
        coverage = build_match_odds_coverage(odds_ctx) if odds_ctx else None

        book_odds, has_odds, derived = _book_odds_for_market(result, market.market_key, coverage)
        suitable = has_odds and (include_derived_pricing or not derived)

        candidate = ParlayMarketCandidate(
            match_id=meta.match_id,
            match_key=f"{mid_str}:{meta.home_team.name}:{meta.away_team.name}" if mid_str else None,
            home_team=meta.home_team.name,
            away_team=meta.away_team.name,
            competition_code=meta.competition_code,
            market_key=market.market_key,
            probability=float(market.probability),
            book_odds=book_odds,
            has_odds=has_odds,
            derived_odds=derived,
            suitable_for_pricing=suitable,
            recommended_by_model=True,
        )

        if suitable:
            pricing.append(candidate)
        else:
            candidate.note = "odds_unavailable"
            no_odds.append(candidate)

    pricing.sort(key=lambda c: c.probability, reverse=True)
    no_odds.sort(key=lambda c: c.probability, reverse=True)

    return ParlayCandidateSplit(
        pricing_candidates=pricing,
        no_odds_candidates=no_odds,
    )
