"""Single public entrypoint for normalized odds ingestion (v1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from football_agent.odds.adapters.base import OddsAdapter
from football_agent.odds.models import (
    MatchOddsContext,
    OddsMarketQuote,
    OddsMarketsBlock,
    OddsMeta,
    OddsProvenance,
)


MARKET_FIELDS: tuple[str, ...] = (
    "home_win",
    "away_win",
    "double_chance_1x",
    "double_chance_x2",
    "btts_yes",
    "home_team_to_score_yes",
    "away_team_to_score_yes",
    "over_1_5",
    "under_3_5",
)


class OddsIngestionService:
    """
    Ingestion façade for normalized odds (v1).

    - hides backend behind OddsAdapter
    - returns typed MatchOddsContext
    - does NOT integrate with OpenClaw runtime, Flashscore, merge, snapshots, or scorers
    """

    def __init__(self, adapter: OddsAdapter) -> None:
        self._adapter = adapter

    def get_odds_for_fixture(self, fixture_id_or_filename: str) -> Optional[MatchOddsContext]:
        raw = self._adapter.fetch_odds_raw(fixture_id_or_filename)
        if not raw:
            return None
        return self._map_raw_to_context(raw)

    def _map_raw_to_context(self, raw: Dict[str, Any]) -> MatchOddsContext:
        now = datetime.now(timezone.utc)

        meta = OddsMeta(
            fixture_id=str(raw.get("fixture_id") or raw.get("id") or ""),
            match_id=raw.get("match_id"),
            home_team=str(raw.get("home_team") or ""),
            away_team=str(raw.get("away_team") or ""),
            competition_name=raw.get("competition_name"),
            kickoff_utc=self._parse_dt(raw.get("kickoff_utc")),
            collected_at_utc=self._parse_dt(raw.get("collected_at_utc")) or now,
            source_url=raw.get("source_url"),
            query_string=raw.get("query_string"),
            odds_format="DECIMAL",
        )

        raw_markets = raw.get("markets") or {}
        markets = self._map_markets(raw_markets if isinstance(raw_markets, dict) else {})

        missing_markets = [name for name in MARKET_FIELDS if getattr(markets, name) is None]

        warnings: List[str] = list(raw.get("extraction_warnings") or [])
        if meta.fixture_id == "":
            warnings.append("missing fixture_id")

        prov = OddsProvenance(
            backend_name=str(raw.get("backend_name") or "fixture"),
            backend_version=raw.get("backend_version"),
            adapter_version="odds-v1",
            collected_at_utc=meta.collected_at_utc,
            blocks_present=["markets"],
            missing_blocks=[],
            missing_markets=missing_markets,
            extraction_warnings=warnings,
        )

        return MatchOddsContext(meta=meta, markets=markets, provenance=prov)

    def _map_markets(self, raw_markets: Dict[str, Any]) -> OddsMarketsBlock:
        def quote(key: str) -> Optional[OddsMarketQuote]:
            val = raw_markets.get(key)
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return OddsMarketQuote(odds_value=float(val))
            if isinstance(val, dict):
                if "odds_value" not in val and "odd" in val:
                    val = {**val, "odds_value": val.get("odd")}
                try:
                    return OddsMarketQuote.model_validate(val)
                except Exception:
                    return None
            return None

        return OddsMarketsBlock(
            home_win=quote("home_win"),
            away_win=quote("away_win"),
            double_chance_1x=quote("double_chance_1x"),
            double_chance_x2=quote("double_chance_x2"),
            btts_yes=quote("btts_yes"),
            home_team_to_score_yes=quote("home_team_to_score_yes"),
            away_team_to_score_yes=quote("away_team_to_score_yes"),
            over_1_5=quote("over_1_5"),
            under_3_5=quote("under_3_5"),
        )

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

