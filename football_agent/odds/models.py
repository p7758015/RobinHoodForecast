"""Typed normalized odds contract (v1).

Scope (intentionally minimal):
- Supported markets:
  - home_win
  - away_win
  - double_chance_1x
  - double_chance_x2
  - btts_yes
  - home_team_to_score_yes
  - away_team_to_score_yes
  - over_1_5
  - under_3_5

Important:
- Pure DRAW/X market is intentionally NOT included in this v1 contract.
  This prevents accidental dependency on a market we didn't standardize yet.
- Odds format is fixed to DECIMAL for this step.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OddsBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


OddsFormat = Literal["DECIMAL"]
QuoteConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class OddsMeta(OddsBaseModel):
    fixture_id: str
    match_id: Optional[str] = None
    source: str = Field(default="odds")

    home_team: str
    away_team: str
    competition_name: Optional[str] = None
    kickoff_utc: Optional[datetime] = None

    odds_format: OddsFormat = "DECIMAL"

    collected_at_utc: datetime
    source_url: Optional[str] = None
    query_string: Optional[str] = None


class OddsMarketQuote(OddsBaseModel):
    odds_value: float = Field(gt=1.0, description="Decimal odds (e.g. 1.85).")
    bookmaker_name: Optional[str] = None
    market_name_raw: Optional[str] = None
    selection_name_raw: Optional[str] = None
    source_url: Optional[str] = None
    extracted_at_utc: Optional[datetime] = None
    confidence: QuoteConfidence = "UNKNOWN"


class OddsMarketsBlock(OddsBaseModel):
    # 1X2 subset (no DRAW/X in v1)
    home_win: Optional[OddsMarketQuote] = None
    away_win: Optional[OddsMarketQuote] = None

    # Double chance
    double_chance_1x: Optional[OddsMarketQuote] = None
    double_chance_x2: Optional[OddsMarketQuote] = None

    # Both teams to score
    btts_yes: Optional[OddsMarketQuote] = None

    # Team to score (explicit home/away to avoid ambiguity)
    home_team_to_score_yes: Optional[OddsMarketQuote] = None
    away_team_to_score_yes: Optional[OddsMarketQuote] = None

    # Totals
    over_1_5: Optional[OddsMarketQuote] = None
    under_3_5: Optional[OddsMarketQuote] = None


class OddsProvenance(OddsBaseModel):
    backend_name: str
    backend_version: Optional[str] = None
    adapter_version: str = "odds-v1"
    collected_at_utc: datetime

    blocks_present: List[str] = Field(default_factory=list)
    missing_blocks: List[str] = Field(default_factory=list)
    missing_markets: List[str] = Field(default_factory=list)
    extraction_warnings: List[str] = Field(default_factory=list)


class MatchOddsContext(OddsBaseModel):
    meta: OddsMeta
    markets: OddsMarketsBlock
    provenance: OddsProvenance

