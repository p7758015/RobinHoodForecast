"""Single public entrypoint for normalized Flashscore facts ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from football_agent.flashscore.adapters.base import FlashscoreScraperAdapter
from football_agent.flashscore.raw_enrich import assess_block_signals, enrich_http_flashscore_raw
from football_agent.flashscore.models import (
    FlashscoreFormBlock,
    FlashscoreH2HBlock,
    FlashscoreMatchFacts,
    FlashscoreMeta,
    FlashscoreProvenance,
    FlashscoreScheduleRaw,
    FlashscoreSeasonContextInputs,
    FlashscoreSquadRaw,
    FlashscoreStandings,
    FlashscoreStatsRaw,
    FlashscoreTeamFormBlock,
)


class FlashscoreIngestionService:
    """
    Ingestion façade for Flashscore factual data.

    - hides concrete scraper backend behind FlashscoreScraperAdapter
    - returns typed FlashscoreMatchFacts instances
    - does NOT talk to v2 snapshots / scorers / OpenClaw
    """

    def __init__(self, adapter: FlashscoreScraperAdapter) -> None:
        self._adapter = adapter

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def get_facts_for_date(
        self,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> List[FlashscoreMatchFacts]:
        raw_list = self._adapter.fetch_matches_for_date(date_str, competition_code)
        return [self._map_raw_to_facts(raw) for raw in raw_list]

    def get_facts_for_match(self, match_id_or_url: str) -> Optional[FlashscoreMatchFacts]:
        raw = self._adapter.fetch_match_raw(match_id_or_url)
        if not raw:
            return None
        return self._map_raw_to_facts(raw)

    # ------------------------------------------------------------------ #
    # Raw → normalized mapping                                           #
    # ------------------------------------------------------------------ #

    _PROVENANCE_BLOCK_NAMES = {
        "standings": "standings",
        "season_context": "season_context_inputs",
        "form": "form",
        "h2h": "h2h",
        "squad_raw": "squad_raw",
        "schedule_raw": "schedule_raw",
        "stats_raw": "stats_raw",
    }

    def _map_raw_to_facts(self, raw: Dict[str, Any]) -> FlashscoreMatchFacts:
        """
        Map one raw scraper record into FlashscoreMatchFacts.

        The raw shape is backend-specific; here we expect a dict with keys similar to
        what a Flashscore-oriented scraper would output. Missing keys are tolerated.
        """
        raw = enrich_http_flashscore_raw(raw)
        signals = assess_block_signals(raw)

        meta = self._map_meta(raw)
        standings = self._map_standings(raw.get("standings") or {}) if signals["standings"] else None
        season_ctx = (
            self._map_season_context(raw.get("season_context") or {})
            if signals["season_context"]
            else None
        )
        form = self._map_form(raw.get("form") or {}) if signals["form"] else None
        h2h = self._map_h2h(raw.get("h2h") or {}) if signals["h2h"] else None
        squad = self._map_squad(raw.get("squad_raw") or {}) if signals["squad_raw"] else None
        schedule = self._map_schedule(raw.get("schedule_raw") or {}) if signals["schedule_raw"] else None
        stats = self._map_stats(raw.get("stats_raw") or {}) if signals["stats_raw"] else None

        present_blocks = [
            self._PROVENANCE_BLOCK_NAMES[k]
            for k, ok in signals.items()
            if ok
        ]
        missing_blocks = [
            self._PROVENANCE_BLOCK_NAMES[k]
            for k, ok in signals.items()
            if not ok
        ]
        parsing_warnings: List[str] = list(raw.get("enrichment_warnings") or [])

        provenance = FlashscoreProvenance(
            scraper_backend_name=str(raw.get("scraper_backend_name") or "fixture"),
            scraper_backend_version=raw.get("scraper_backend_version"),
            adapter_version="flashscore-facts-v2",
            collected_at_utc=self._safe_dt(raw.get("collected_at_utc")),
            blocks_present=present_blocks,
            missing_blocks=missing_blocks,
            parsing_warnings=parsing_warnings,
        )

        return FlashscoreMatchFacts(
            meta=meta,
            standings=standings,
            season_context_inputs=season_ctx,
            form=form,
            h2h=h2h,
            squad_raw=squad,
            schedule_raw=schedule,
            stats_raw=stats,
            provenance=provenance,
        )

    @staticmethod
    def _safe_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None

    @staticmethod
    def _map_meta(raw: Dict[str, Any]) -> FlashscoreMeta:
        from football_agent.services.competition_classifier import refine_meta_tournament_type

        meta = FlashscoreMeta(
            match_id=str(raw.get("match_id") or raw.get("id") or ""),
            source_url=str(raw.get("source_url") or ""),
            competition_name=str(raw.get("competition_name") or raw.get("league_name") or "Unknown competition"),
            competition_country=raw.get("competition_country"),
            season=raw.get("season"),
            stage=raw.get("stage"),
            round=raw.get("round"),
            tournament_type=FlashscoreIngestionService._parse_tournament_type(raw.get("tournament_type")),
            kickoff_utc=FlashscoreIngestionService._parse_datetime(raw.get("kickoff_utc")),
            home_team_name=str(raw.get("home_team_name") or raw.get("home") or ""),
            away_team_name=str(raw.get("away_team_name") or raw.get("away") or ""),
            status=str(raw.get("status") or "SCHEDULED"),
        )
        return refine_meta_tournament_type(meta)

    @staticmethod
    def _parse_tournament_type(value: Any):
        from football_agent.domain.enums_v2 import TournamentType

        if not value:
            return TournamentType.LEAGUE_REGULAR
        if isinstance(value, TournamentType):
            return value
        try:
            return TournamentType[str(value)]
        except Exception:
            try:
                return TournamentType(value)
            except Exception:
                return TournamentType.LEAGUE_REGULAR

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

    @staticmethod
    def _map_standings(raw: Dict[str, Any]) -> Optional[FlashscoreStandings]:
        if not raw:
            return None
        return FlashscoreStandings(**raw)

    @staticmethod
    def _map_season_context(raw: Dict[str, Any]) -> Optional[FlashscoreSeasonContextInputs]:
        if not raw:
            return None
        return FlashscoreSeasonContextInputs(**raw)

    @staticmethod
    def _map_form(raw: Dict[str, Any]) -> Optional[FlashscoreFormBlock]:
        if not raw:
            return None

        def team_block(side: str) -> Optional[FlashscoreTeamFormBlock]:
            data = raw.get(side) or {}
            if not data:
                return None
            return FlashscoreTeamFormBlock(**data)

        home_tb = team_block("home")
        away_tb = team_block("away")
        if not home_tb and not away_tb:
            return None
        return FlashscoreFormBlock(home=home_tb, away=away_tb)

    @staticmethod
    def _map_h2h(raw: Dict[str, Any]) -> Optional[FlashscoreH2HBlock]:
        if not raw:
            return None
        return FlashscoreH2HBlock(**raw)

    @staticmethod
    def _map_squad(raw: Dict[str, Any]) -> Optional[FlashscoreSquadRaw]:
        if not raw:
            return None
        return FlashscoreSquadRaw(**raw)

    @staticmethod
    def _map_schedule(raw: Dict[str, Any]) -> Optional[FlashscoreScheduleRaw]:
        if not raw:
            return None
        return FlashscoreScheduleRaw(**raw)

    @staticmethod
    def _map_stats(raw: Dict[str, Any]) -> Optional[FlashscoreStatsRaw]:
        if not raw:
            return None
        return FlashscoreStatsRaw(**raw)

