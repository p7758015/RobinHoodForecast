from __future__ import annotations

from math import log, prod
from typing import List, Tuple

from football_agent.config import EXPRESS_MAX_ODDS, EXPRESS_MIN_ODDS, EXPRESS_MIN_PROBABILITY
from football_agent.domain.models import ExpressBet, ExpressEvent, MatchAnalysisResult, MarketPrediction


def build_express(
    all_results: List[MatchAnalysisResult],
    target_odds: float,
    max_events: int = 6,
    tolerance: float = 0.20,
) -> ExpressBet:
    # Шаг 1: Собрать кандидатов — для каждого матча берём best_market
    candidates: List[Tuple[MatchAnalysisResult, MarketPrediction]] = []
    for r in all_results:
        m = r.best_market
        if (
            m.probability >= EXPRESS_MIN_PROBABILITY
            and m.odds is not None
            and EXPRESS_MIN_ODDS <= m.odds <= EXPRESS_MAX_ODDS
        ):
            candidates.append((r, m))

    if not candidates:
        # fallback: взять top-2 по вероятности без фильтра по odds
        sorted_all = sorted(all_results, key=lambda r: r.best_market.probability, reverse=True)
        top2 = sorted_all[:2]
        return ExpressBet(
            events=[ExpressEvent(match=r.match, market=r.best_market) for r in top2],
            total_odds=prod((r.best_market.odds or 1.0) for r in top2),
            total_probability=prod(r.best_market.probability for r in top2),
            target_odds=target_odds,
        )

    # Шаг 2: Сортировка по probability * log(odds)
    candidates.sort(key=lambda x: x[1].probability * log(x[1].odds), reverse=True)

    # Шаг 3: Жадный перебор с разными стартовыми точками
    best_express: List[Tuple[MatchAnalysisResult, MarketPrediction]] | None = None
    best_diff = float("inf")

    for start in range(min(5, len(candidates))):
        express: List[Tuple[MatchAnalysisResult, MarketPrediction]] = []
        product = 1.0
        for r, m in candidates[start:]:
            if len(express) >= max_events:
                break
            new_product = product * (m.odds or 1.0)
            if new_product > target_odds * 1.5:
                continue
            express.append((r, m))
            product = new_product
            diff = abs(product - target_odds)
            if product >= target_odds * (1 - tolerance) and diff < best_diff:
                best_diff = diff
                best_express = list(express)

    if best_express is None:
        best_express = candidates[:2]

    events = [ExpressEvent(match=r.match, market=m) for r, m in best_express]
    total_odds = prod(m.odds for _, m in best_express if m.odds is not None)
    total_prob = prod(m.probability for _, m in best_express)

    return ExpressBet(
        events=events,
        total_odds=round(total_odds, 2),
        total_probability=round(total_prob, 4),
        target_odds=target_odds,
    )

