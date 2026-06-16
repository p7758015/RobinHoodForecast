"""Pre-kickoff Flashscore collector odds refresh (Refresh A)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from football_agent import config
from football_agent.collectors.odds_bridge import SOURCE_FLASHSCORE_COLLECTOR, collector_odds_to_context
from football_agent.odds.models import MatchOddsContext
from football_agent.services.match_collection_service import MatchCollectionService, MatchCollectionServiceResult
from football_agent.services.odds_freshness import freshness_status_for, is_odds_stale
from football_agent.services.odds_refresh_store import OddsRefreshRecord, OddsRefreshStore
from football_agent.storage.match_key import build_match_key

logger = logging.getLogger(__name__)


@dataclass
class OddsRefreshResult:
    success: bool
    refreshed: bool = False
    skipped: bool = False
    warnings: List[str] = field(default_factory=list)
    match_key: Optional[str] = None
    match_url: Optional[str] = None
    before_collected_at_utc: Optional[datetime] = None
    after_collected_at_utc: Optional[datetime] = None
    odds_context: Optional[MatchOddsContext] = None
    store_path: Optional[str] = None
    error_message: Optional[str] = None

    def to_summary_dict(self) -> dict:
        return {
            "success": self.success,
            "refreshed": self.refreshed,
            "skipped": self.skipped,
            "match_key": self.match_key,
            "match_url": self.match_url,
            "before_collected_at_utc": (
                self.before_collected_at_utc.isoformat() if self.before_collected_at_utc else None
            ),
            "after_collected_at_utc": (
                self.after_collected_at_utc.isoformat() if self.after_collected_at_utc else None
            ),
            "warnings": list(self.warnings),
            "store_path": self.store_path,
            "error_message": self.error_message,
        }


class OddsRefreshService:
    """
    Re-fetch Flashscore collector odds and persist a refresh snapshot.

    Uses the same MatchCollectionService + collector_odds_to_context path as live pipeline.
    Does not call OpenClaw/enrichment. Fail-soft on missing odds.
    """

    def __init__(
        self,
        scraper_url: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
        store: Optional[OddsRefreshStore] = None,
        max_age_minutes: Optional[int] = None,
        pre_kickoff_window_minutes: Optional[int] = None,
    ) -> None:
        base = (scraper_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
        self._scraper_url = base or None
        self._api_key = api_key or config.FLASHSCORE_SCRAPER_API_KEY
        self._timeout_s = timeout_s or config.FLASHSCORE_SCRAPER_TIMEOUT_S
        self._store = store or OddsRefreshStore()
        self._max_age = max_age_minutes if max_age_minutes is not None else config.ODDS_REFRESH_MAX_AGE_MINUTES
        self._pre_window = (
            pre_kickoff_window_minutes
            if pre_kickoff_window_minutes is not None
            else config.ODDS_REFRESH_PRE_KICKOFF_WINDOW_MINUTES
        )

    def refresh_for_match_url(
        self,
        match_url: str,
        *,
        force: bool = False,
        now_utc: Optional[datetime] = None,
    ) -> OddsRefreshResult:
        if not config.USE_COLLECTOR_LAYER:
            return OddsRefreshResult(
                success=False,
                warnings=["odds_refresh_requires_collector_layer"],
                error_message="Set USE_COLLECTOR_LAYER=true for odds refresh",
                match_url=match_url.strip(),
            )
        if not self._scraper_url:
            return OddsRefreshResult(
                success=False,
                warnings=["odds_refresh_scraper_not_configured"],
                error_message="FLASHSCORE_SCRAPER_URL not configured",
                match_url=match_url.strip(),
            )

        now = now_utc or datetime.now(timezone.utc)
        svc = MatchCollectionService(
            self._scraper_url,
            api_key=self._api_key,
            timeout_s=self._timeout_s,
        )
        try:
            collection = svc.collect_for_url(match_url.strip())
        except Exception as exc:
            logger.exception("Odds refresh collection failed url=%s", match_url)
            return OddsRefreshResult(
                success=False,
                warnings=[f"odds_refresh_collection_error:{exc}"],
                error_message=str(exc),
                match_url=match_url.strip(),
            )

        return self._finish_collection(
            collection,
            match_url=match_url.strip(),
            force=force,
            now_utc=now,
        )

    def refresh_for_teams(
        self,
        home: str,
        away: str,
        date_str: str,
        *,
        competition_code: Optional[str] = None,
        force: bool = False,
        now_utc: Optional[datetime] = None,
    ) -> OddsRefreshResult:
        if not config.USE_COLLECTOR_LAYER:
            return OddsRefreshResult(
                success=False,
                warnings=["odds_refresh_requires_collector_layer"],
                error_message="Set USE_COLLECTOR_LAYER=true for odds refresh",
            )
        if not self._scraper_url:
            return OddsRefreshResult(
                success=False,
                warnings=["odds_refresh_scraper_not_configured"],
                error_message="FLASHSCORE_SCRAPER_URL not configured",
            )

        now = now_utc or datetime.now(timezone.utc)
        svc = MatchCollectionService(
            self._scraper_url,
            api_key=self._api_key,
            timeout_s=self._timeout_s,
        )
        try:
            collection = svc.collect_for_teams(
                home.strip(),
                away.strip(),
                date_str.strip(),
                competition_code=competition_code,
            )
        except Exception as exc:
            logger.exception("Odds refresh collection failed teams=%s vs %s", home, away)
            return OddsRefreshResult(
                success=False,
                warnings=[f"odds_refresh_collection_error:{exc}"],
                error_message=str(exc),
            )

        return self._finish_collection(collection, force=force, now_utc=now)

    def _finish_collection(
        self,
        collection: MatchCollectionServiceResult,
        *,
        match_url: Optional[str] = None,
        force: bool = False,
        now_utc: datetime,
    ) -> OddsRefreshResult:
        warnings: List[str] = list(collection.warnings or [])

        if collection.aborted or not collection.success or not collection.facts:
            return OddsRefreshResult(
                success=False,
                warnings=warnings + ["odds_refresh_failed_collection"],
                error_message=collection.error_message or "collection failed",
                match_url=match_url,
            )

        facts = collection.facts
        bundle = collection.bundle
        match_key = (
            bundle.match_key
            if bundle
            else build_match_key(
                competition=facts.meta.competition_name or "unknown",
                kickoff_utc=facts.meta.kickoff_utc,
                home_team=facts.meta.home_team_name,
                away_team=facts.meta.away_team_name,
            )
        )

        store_file = self._store.load(match_key)
        before_collected = (
            store_file.current.collected_at_utc if store_file.current is not None else None
        )

        if (
            not force
            and store_file.current is not None
            and not is_odds_stale(
                store_file.current.collected_at_utc,
                facts.meta.kickoff_utc,
                now_utc,
                max_age_minutes=self._max_age,
                pre_kickoff_window_minutes=self._pre_window,
            )
        ):
            warnings.append("odds_refresh_skipped_already_fresh")
            return OddsRefreshResult(
                success=True,
                refreshed=False,
                skipped=True,
                warnings=warnings,
                match_key=match_key,
                match_url=match_url or facts.meta.source_url,
                before_collected_at_utc=before_collected,
                after_collected_at_utc=store_file.current.collected_at_utc,
                odds_context=store_file.current.odds_context,
            )

        odds_ctx = collector_odds_to_context(bundle, facts) if bundle else None
        if odds_ctx is None:
            warnings.append("odds_refresh_failed_no_collector_odds")
            return OddsRefreshResult(
                success=True,
                refreshed=False,
                skipped=False,
                warnings=warnings,
                match_key=match_key,
                match_url=match_url or facts.meta.source_url,
                before_collected_at_utc=before_collected,
                error_message="collector odds block missing or empty",
            )

        refreshed_at = now_utc
        freshness = freshness_status_for(
            odds_ctx.meta.collected_at_utc,
            facts.meta.kickoff_utc,
            now_utc,
            max_age_minutes=self._max_age,
            pre_kickoff_window_minutes=self._pre_window,
        )
        odds_ctx = odds_ctx.model_copy(
            update={
                "provenance": odds_ctx.provenance.model_copy(
                    update={
                        "freshness_status": freshness,  # type: ignore[arg-type]
                        "is_stale": False,
                        "last_refreshed_at_utc": refreshed_at,
                    },
                ),
            },
        )

        record = OddsRefreshRecord(
            match_key=match_key,
            match_url=match_url or facts.meta.source_url,
            kickoff_utc=facts.meta.kickoff_utc,
            collected_at_utc=odds_ctx.meta.collected_at_utc,
            refreshed_at_utc=refreshed_at,
            is_stale=False,
            source=SOURCE_FLASHSCORE_COLLECTOR,
            warnings=warnings,
            odds_context=odds_ctx,
        )
        path = self._store.save_current(record)
        warnings.append("odds_refresh_completed")

        return OddsRefreshResult(
            success=True,
            refreshed=True,
            skipped=False,
            warnings=warnings,
            match_key=match_key,
            match_url=record.match_url,
            before_collected_at_utc=before_collected,
            after_collected_at_utc=odds_ctx.meta.collected_at_utc,
            odds_context=odds_ctx,
            store_path=str(path),
        )
