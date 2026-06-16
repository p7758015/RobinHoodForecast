"""
Build per-match odds coverage from MatchOddsContext (Phase Evaluation A).
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Set

from football_agent.odds.coverage_models import (
    COVERAGE_MARKET_KEYS,
    MarketOddsCoverageEntry,
    MatchOddsCoverage,
    PricingQuality,
)
from football_agent.odds.models import MatchOddsContext, OddsMarketQuote
from football_agent.odds.service import MARKET_FIELDS

_DERIVED_WARNING_RE = re.compile(r"collector_odds_derived:([A-Z_,]+)")


def _derived_keys_from_provenance(ctx: MatchOddsContext) -> Set[str]:
    from football_agent.collectors.odds_bridge import COLLECTOR_TO_SERVICE_MARKET

    derived: Set[str] = set()
    for warning in ctx.provenance.extraction_warnings or []:
        m = _DERIVED_WARNING_RE.search(str(warning))
        if not m:
            continue
        for token in m.group(1).split(","):
            token = token.strip()
            service_key = COLLECTOR_TO_SERVICE_MARKET.get(token)
            if service_key:
                derived.add(service_key)
    return derived


def _pricing_from_quote(
    quote: Optional[OddsMarketQuote],
    *,
    derived: bool,
    source: Optional[str],
) -> MarketOddsCoverageEntry:
    has = quote is not None and quote.odds_value is not None and quote.odds_value > 1.0
    if not has:
        return MarketOddsCoverageEntry(
            market_key="",
            has_odds=False,
            suitable_for_pricing=False,
            pricing_quality="none",
            source=source,
        )
    is_derived = derived or (
        quote.selection_name_raw is not None and str(quote.selection_name_raw).startswith("derived:")
    )
    quality: PricingQuality = "derived" if is_derived else "book"
    return MarketOddsCoverageEntry(
        market_key="",
        has_odds=True,
        odds_value=float(quote.odds_value),
        derived=is_derived,
        source=source or quote.bookmaker_name,
        suitable_for_pricing=True,
        pricing_quality=quality,
    )


def build_match_odds_coverage(
    ctx: Optional[MatchOddsContext],
    *,
    predicted_probabilities: Optional[Dict[str, float]] = None,
) -> MatchOddsCoverage:
    """
    Build coverage from MatchOddsContext (or empty shell when ctx is None).

    Pricing policy:
    - Book quotes: suitable_for_pricing=True, pricing_quality=book
    - Derived double-chance: suitable_for_pricing=True, pricing_quality=derived
    - Missing: suitable_for_pricing=False
    """
    predicted_probabilities = predicted_probabilities or {}

    if ctx is None:
        markets = {
            key: MarketOddsCoverageEntry(
                market_key=key,
                has_odds=False,
                has_prediction=key in predicted_probabilities,
                predicted_probability=predicted_probabilities.get(key),
            )
            for key in COVERAGE_MARKET_KEYS
        }
        return MatchOddsCoverage(
            markets=markets,
            missing_market_keys=list(COVERAGE_MARKET_KEYS),
        )

    derived_keys = _derived_keys_from_provenance(ctx)
    source = ctx.meta.source
    markets: Dict[str, MarketOddsCoverageEntry] = {}

    for key in COVERAGE_MARKET_KEYS:
        if key == "draw":
            markets[key] = MarketOddsCoverageEntry(
                market_key=key,
                has_odds=False,
                source=source,
                has_prediction=key in predicted_probabilities,
                predicted_probability=predicted_probabilities.get(key),
            )
            continue
        if key not in MARKET_FIELDS:
            continue

        quote = getattr(ctx.markets, key, None)
        entry = _pricing_from_quote(quote, derived=key in derived_keys, source=source)
        entry.market_key = key
        entry.has_prediction = key in predicted_probabilities
        entry.predicted_probability = predicted_probabilities.get(key)
        markets[key] = entry

    missing = [k for k, v in markets.items() if not v.has_odds]
    real_count = sum(1 for v in markets.values() if v.has_odds and not v.derived)
    derived_count = sum(1 for v in markets.values() if v.has_odds and v.derived)
    has_any = real_count + derived_count > 0

    return MatchOddsCoverage(
        match_id=ctx.meta.match_id or ctx.meta.fixture_id,
        home_team=ctx.meta.home_team,
        away_team=ctx.meta.away_team,
        source=source,
        collected_at_utc=ctx.meta.collected_at_utc,
        is_stale=bool(ctx.provenance.is_stale),
        freshness_status=str(ctx.provenance.freshness_status or "unknown"),
        markets=markets,
        has_any_odds=has_any,
        odds_usable_for_parlay=any(v.suitable_for_pricing for v in markets.values()),
        has_1x2_odds=bool(
            markets.get("home_win", MarketOddsCoverageEntry(market_key="home_win")).has_odds
            and markets.get("away_win", MarketOddsCoverageEntry(market_key="away_win")).has_odds
        ),
        has_double_chance_odds=bool(
            markets.get("double_chance_1x", MarketOddsCoverageEntry(market_key="double_chance_1x")).has_odds
            or markets.get("double_chance_x2", MarketOddsCoverageEntry(market_key="double_chance_x2")).has_odds
        ),
        has_btts_odds=bool(markets.get("btts_yes", MarketOddsCoverageEntry(market_key="btts_yes")).has_odds),
        has_totals_odds=bool(
            markets.get("over_1_5", MarketOddsCoverageEntry(market_key="over_1_5")).has_odds
            or markets.get("under_3_5", MarketOddsCoverageEntry(market_key="under_3_5")).has_odds
        ),
        real_market_count=real_count,
        derived_market_count=derived_count,
        missing_market_keys=missing,
    )


def enrich_odds_context_with_coverage(
    ctx: Optional[MatchOddsContext],
    *,
    predicted_probabilities: Optional[Dict[str, float]] = None,
) -> Optional[MatchOddsContext]:
    if ctx is None:
        return None
    coverage = build_match_odds_coverage(ctx, predicted_probabilities=predicted_probabilities)
    return ctx.model_copy(update={"coverage": coverage})
