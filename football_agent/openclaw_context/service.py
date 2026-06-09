"""Single public entrypoint for normalized OpenClaw match context ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from football_agent.openclaw_context.adapters.base import OpenClawContextAdapter
from football_agent.openclaw_context.models import (
    OpenClawCoachContext,
    OpenClawContextMeta,
    OpenClawContextProvenance,
    OpenClawFatigueScheduleContext,
    OpenClawMatchContext,
    OpenClawMotivationNarrative,
    OpenClawNewsBlock,
    OpenClawNewsItem,
    OpenClawPlayerContextItem,
    OpenClawSquadContext,
    ReliabilityLevel,
)


class OpenClawContextIngestionService:
    """
    Ingestion façade for OpenClaw context (secondary context/news layer).

    - hides concrete backend behind OpenClawContextAdapter
    - returns typed OpenClawMatchContext instances
    - does NOT talk to Flashscore facts, merge, snapshots, scorers, or pipeline
    """

    def __init__(self, adapter: OpenClawContextAdapter) -> None:
        self._adapter = adapter

    def get_context_for_fixture(self, fixture_id_or_filename: str) -> Optional[OpenClawMatchContext]:
        raw = self._adapter.fetch_context_raw(fixture_id_or_filename)
        if not raw:
            return None
        return self._map_raw_to_context(raw)

    # ------------------------------------------------------------------ #
    # Raw → normalized mapping                                           #
    # ------------------------------------------------------------------ #

    def _map_raw_to_context(self, raw: Dict[str, Any]) -> OpenClawMatchContext:
        now = datetime.now(timezone.utc)

        meta = OpenClawContextMeta(
            match_id=raw.get("match_id"),
            query_home_team=str(raw.get("query_home_team") or ""),
            query_away_team=str(raw.get("query_away_team") or ""),
            query_home_team_normalized=raw.get("query_home_team_normalized"),
            query_away_team_normalized=raw.get("query_away_team_normalized"),
            query_competition_name=raw.get("query_competition_name"),
            query_kickoff_utc=self._parse_dt(raw.get("query_kickoff_utc")),
            query_date=self._parse_date(raw.get("query_date")),
            query_string=raw.get("query_string"),
            collected_at_utc=self._parse_dt(raw.get("collected_at_utc")) or now,
            context_window_hours=raw.get("context_window_hours"),
        )

        extraction_warnings: List[str] = list(raw.get("extraction_warnings") or [])

        news = self._map_news(raw.get("news"))
        squad = self._map_squad(raw.get("squad_context"))
        coach = self._map_block(OpenClawCoachContext, raw.get("coach_context"))
        motivation = self._map_block(OpenClawMotivationNarrative, raw.get("motivation_narrative"))
        fatigue = self._map_block(OpenClawFatigueScheduleContext, raw.get("fatigue_schedule_context"))

        blocks_present: List[str] = []
        missing_blocks: List[str] = []

        def mark(name: str, val: object) -> None:
            (blocks_present if val is not None else missing_blocks).append(name)

        mark("news", news)
        mark("squad_context", squad)
        mark("coach_context", coach)
        mark("motivation_narrative", motivation)
        mark("fatigue_schedule_context", fatigue)

        provenance = OpenClawContextProvenance(
            backend_name=str(raw.get("backend_name") or "fixture"),
            backend_version=raw.get("backend_version"),
            adapter_version="openclaw-context-v1",
            collected_at_utc=meta.collected_at_utc,
            blocks_present=blocks_present,
            missing_blocks=missing_blocks,
            extraction_warnings=extraction_warnings,
        )

        return OpenClawMatchContext(
            meta=meta,
            news=news,
            squad_context=squad,
            coach_context=coach,
            motivation_narrative=motivation,
            fatigue_schedule_context=fatigue,
            provenance=provenance,
        )

    @staticmethod
    def _map_block(model_cls, raw_val):  # noqa: ANN001
        if not raw_val or not isinstance(raw_val, dict):
            return None
        return model_cls.model_validate(raw_val)

    def _map_news(self, raw_news: Any) -> Optional[OpenClawNewsBlock]:
        if not raw_news or not isinstance(raw_news, dict):
            return None

        def items(key: str) -> List[OpenClawNewsItem]:
            arr = raw_news.get(key) or []
            out: List[OpenClawNewsItem] = []
            for x in arr:
                if isinstance(x, dict):
                    out.append(OpenClawNewsItem.model_validate(x))
            return out

        block = OpenClawNewsBlock(
            home_news_items=items("home_news_items"),
            away_news_items=items("away_news_items"),
            match_news_items=items("match_news_items"),
        )
        self._fill_news_aggregates(block)
        return block

    def _fill_news_aggregates(self, block: OpenClawNewsBlock) -> None:
        all_items = block.home_news_items + block.away_news_items + block.match_news_items
        sources: Set[str] = set()
        high = 0
        for it in all_items:
            if it.source_name:
                sources.add(it.source_name.strip().lower())
            if it.reliability_level == "HIGH":
                high += 1
        block.source_count = len(sources) if sources else 0
        block.high_confidence_count = high
        # Early heuristic: conflicting if we have both HIGH and LOW items across the set.
        levels = {it.reliability_level for it in all_items}
        block.conflicting_reports_flag = ("HIGH" in levels and "LOW" in levels) if all_items else False

    def _map_squad(self, raw_squad: Any) -> Optional[OpenClawSquadContext]:
        if not raw_squad or not isinstance(raw_squad, dict):
            return None

        squad = OpenClawSquadContext.model_validate(raw_squad)
        self._fill_squad_aggregates(squad)
        return squad

    def _fill_squad_aggregates(self, squad: OpenClawSquadContext) -> None:
        self._fill_side_aggregates(squad.home)
        self._fill_side_aggregates(squad.away)

    def _fill_side_aggregates(self, side_ctx) -> None:  # noqa: ANN001
        items = (
            side_ctx.missing_players_context
            + side_ctx.returning_players_context
        )
        sources: Set[str] = set()
        high = 0
        statuses_by_player: Dict[str, Set[str]] = {}

        for it in items:
            if isinstance(it, OpenClawPlayerContextItem):
                if it.source_name:
                    sources.add(it.source_name.strip().lower())
                if it.confidence == "HIGH":
                    high += 1
                key = (it.player_name or "").strip().lower()
                if key:
                    statuses_by_player.setdefault(key, set()).add(it.status)

        side_ctx.source_count = len(sources) if sources else 0
        side_ctx.high_confidence_count = high
        side_ctx.conflicting_reports_flag = any(len(st) > 1 for st in statuses_by_player.values())

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

    @staticmethod
    def _parse_date(value: Any):
        from datetime import date as _date

        if isinstance(value, _date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return _date.fromisoformat(value)
            except Exception:
                return None
        return None

