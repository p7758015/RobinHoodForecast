"""Odds coverage data models (Phase Evaluation A)."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _CoverageBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

PricingQuality = Literal["book", "derived", "none"]

COVERAGE_MARKET_KEYS: tuple[str, ...] = (
    "home_win",
    "draw",
    "away_win",
    "double_chance_1x",
    "double_chance_x2",
    "btts_yes",
    "over_1_5",
    "under_3_5",
)


class MarketOddsCoverageEntry(_CoverageBase):
    """Per-market odds availability for one match."""

    market_key: str
    has_odds: bool = False
    odds_value: Optional[float] = Field(default=None, gt=1.0)
    derived: bool = False
    source: Optional[str] = None
    suitable_for_pricing: bool = False
    pricing_quality: PricingQuality = "none"
    has_prediction: bool = False
    predicted_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class MatchOddsCoverage(_CoverageBase):
    """Aggregate odds coverage snapshot for one match."""

    match_id: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    source: Optional[str] = None
    collected_at_utc: Optional[datetime] = None
    is_stale: bool = False
    freshness_status: str = "unknown"

    markets: Dict[str, MarketOddsCoverageEntry] = Field(default_factory=dict)

    has_any_odds: bool = False
    odds_usable_for_parlay: bool = False

    has_1x2_odds: bool = False
    has_double_chance_odds: bool = False
    has_btts_odds: bool = False
    has_totals_odds: bool = False

    real_market_count: int = 0
    derived_market_count: int = 0
    missing_market_keys: List[str] = Field(default_factory=list)
