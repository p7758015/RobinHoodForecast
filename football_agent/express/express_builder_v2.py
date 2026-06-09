"""
ExpressBuilderV2: MatchPredictionResultV2 list → ExpressBetV2.

Trusts scorer express_safety; only assembles legs toward target_odds.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

from football_agent.config import (
    EXPRESS_MAX_ODDS,
    EXPRESS_MIN_LEG_ODDS,
    EXPRESS_MIN_ODDS,
    EXPRESS_MIN_PROBABILITY,
)
from football_agent.domain.models_v2 import (
    ExpressBetV2,
    ExpressEventV2,
    MarketPredictionV2,
    MatchPredictionResultV2,
)

logger = logging.getLogger(__name__)


class ExpressBuilderV2:
    """Greedy express assembly over scorer-approved predictions."""

    def build_express(
        self,
        results: List[MatchPredictionResultV2],
        target_odds: float,
        max_events: int = 5,
        tolerance: float = 0.20,
    ) -> Optional[ExpressBetV2]:
        if target_odds <= 1.0:
            logger.warning("Express target_odds must be > 1.0, got %s", target_odds)
            return None

        candidates = self._collect_candidates(results)
        if not candidates:
            logger.info("Express v2: no candidates after filtering (%d inputs)", len(results))
            return None

        candidates.sort(key=self._candidate_score, reverse=True)
        best_combo, best_diff = self._greedy_search(candidates, target_odds, max_events, tolerance)

        if not best_combo:
            return None

        return self._to_express_bet(best_combo, target_odds, best_diff, tolerance)

    def _collect_candidates(
        self,
        results: List[MatchPredictionResultV2],
    ) -> List[MatchPredictionResultV2]:
        seen_match_ids: set[int] = set()
        candidates: List[MatchPredictionResultV2] = []

        for result in results:
            try:
                if not result.express_safety.allow_for_express:
                    continue
                market = result.best_market
                if market is None:
                    continue
                mid = result.match_meta.match_id
                if mid in seen_match_ids:
                    continue
                odds = market.book_odds
                if odds is None or odds <= 1.0:
                    continue
                if market.probability < EXPRESS_MIN_PROBABILITY:
                    continue
                if odds < max(EXPRESS_MIN_ODDS, EXPRESS_MIN_LEG_ODDS) or odds > EXPRESS_MAX_ODDS:
                    continue
                seen_match_ids.add(mid)
                candidates.append(result)
            except Exception as e:
                logger.warning(
                    "Skipped express candidate match %s: %s",
                    getattr(result.match_meta, "match_id", "?"),
                    e,
                )
        return candidates

    @staticmethod
    def _candidate_score(result: MatchPredictionResultV2) -> float:
        market = result.best_market
        assert market is not None and market.book_odds is not None
        odds = market.book_odds
        conf = result.overall_confidence_score
        penalty = result.express_safety.penalty_score
        edge_bonus = max(0.0, market.edge or 0.0)
        return (
            market.probability
            * math.log(odds)
            * (0.55 + 0.45 * conf)
            * (1.0 - penalty * 0.35)
            * (1.0 + edge_bonus * 2.0)
        )

    def _greedy_search(
        self,
        candidates: List[MatchPredictionResultV2],
        target_odds: float,
        max_events: int,
        tolerance: float,
    ) -> Tuple[List[MatchPredictionResultV2], float]:
        best_combo: List[MatchPredictionResultV2] = []
        best_diff = float("inf")
        upper_cap = target_odds * (1.0 + tolerance + 0.3)

        for start in range(min(5, len(candidates))):
            combo: List[MatchPredictionResultV2] = []
            product = 1.0
            for result in candidates[start:]:
                if len(combo) >= max_events:
                    break
                odds = result.best_market.book_odds  # type: ignore[union-attr]
                new_product = product * odds
                if new_product > upper_cap and combo:
                    continue
                combo.append(result)
                product = new_product
                diff = abs(product - target_odds)
                if diff < best_diff:
                    best_diff = diff
                    best_combo = list(combo)

        if len(best_combo) < 2 and len(candidates) >= 2:
            best_combo = candidates[: min(max_events, 2)]
            best_diff = abs(self._product_odds(best_combo) - target_odds)

        return best_combo, best_diff

    @staticmethod
    def _product_odds(results: List[MatchPredictionResultV2]) -> float:
        total = 1.0
        for r in results:
            total *= r.best_market.book_odds  # type: ignore[union-attr]
        return total

    @staticmethod
    def _product_probability(results: List[MatchPredictionResultV2]) -> float:
        total = 1.0
        for r in results:
            total *= r.best_market.probability  # type: ignore[union-attr]
        return total

    def _to_express_bet(
        self,
        combo: List[MatchPredictionResultV2],
        target_odds: float,
        diff: float,
        tolerance: float,
    ) -> ExpressBetV2:
        events: List[ExpressEventV2] = []
        for result in combo:
            market = result.best_market
            assert market is not None and market.book_odds is not None
            events.append(
                ExpressEventV2(
                    match_meta=result.match_meta,
                    market_key=market.market_key,
                    probability=market.probability,
                    book_odds=market.book_odds,
                    label=market.label,
                    edge=market.edge,
                )
            )

        total_odds = round(self._product_odds(combo), 2)
        total_prob = round(self._product_probability(combo), 4)
        within = diff <= target_odds * tolerance

        return ExpressBetV2(
            events=events,
            total_odds=total_odds,
            total_probability=total_prob,
            target_odds=target_odds,
            within_tolerance=within,
            selection_notes=(
                f"{len(events)} legs, diff={diff:.2f} vs target {target_odds}"
                + (" (within tolerance)" if within else " (best effort)")
            ),
        )
