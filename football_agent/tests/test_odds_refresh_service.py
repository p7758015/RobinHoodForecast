"""Odds refresh service tests (Refresh A)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from football_agent.collectors.contracts import MatchRef
from football_agent.collectors.orchestrator import MatchCollectorOrchestrator
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.services.match_collection_service import MatchCollectionServiceResult
from football_agent.services.odds_refresh_service import OddsRefreshService
from football_agent.services.odds_refresh_store import OddsRefreshStore

_FIXTURES = Path(__file__).parent / "data"
UTC = timezone.utc


def _collection_with_odds() -> MatchCollectionServiceResult:
    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    raw = enrich_http_flashscore_raw(raw)
    raw["odds"] = {"markets": {"home_win": 1.9, "away_win": 3.5, "btts_yes": 1.7}}
    bundle, trace = MatchCollectorOrchestrator().collect_from_raw(raw, MatchRef())
    facts = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(_FIXTURES)).get_facts_for_match(
        "flashscore_sample_league_match",
    )
    assert facts is not None
    return MatchCollectionServiceResult(
        success=True,
        facts=facts,
        bundle=bundle,
        trace=trace,
        warnings=[],
    )


@patch("football_agent.services.odds_refresh_service.config.USE_COLLECTOR_LAYER", True)
def test_refresh_updates_store_collected_at(tmp_path: Path) -> None:
    store = OddsRefreshStore(root=tmp_path)
    svc = OddsRefreshService("http://localhost:3000", store=store)
    collection = _collection_with_odds()
    now = datetime(2025, 6, 3, 12, 0, tzinfo=UTC)

    with patch.object(svc, "_finish_collection", wraps=svc._finish_collection):
        with patch(
            "football_agent.services.odds_refresh_service.MatchCollectionService.collect_for_url",
            return_value=collection,
        ):
            result = svc.refresh_for_match_url("https://example.com/match", force=True, now_utc=now)

    assert result.success is True
    assert result.refreshed is True
    assert result.after_collected_at_utc is not None
    assert result.store_path is not None
    loaded = store.load(result.match_key or "")
    assert loaded.current is not None
    assert loaded.current.is_stale is False
    assert loaded.current.odds_context.provenance.last_refreshed_at_utc == now


@patch("football_agent.services.odds_refresh_service.config.USE_COLLECTOR_LAYER", True)
def test_refresh_skips_when_already_fresh(tmp_path: Path) -> None:
    store = OddsRefreshStore(root=tmp_path)
    svc = OddsRefreshService("http://localhost:3000", store=store, max_age_minutes=60)
    collection = _collection_with_odds()
    now = datetime(2025, 6, 3, 12, 0, tzinfo=UTC)

    with patch(
        "football_agent.services.odds_refresh_service.MatchCollectionService.collect_for_url",
        return_value=collection,
    ):
        first = svc.refresh_for_match_url("https://example.com/match", force=True, now_utc=now)
        second = svc.refresh_for_match_url("https://example.com/match", now_utc=now + timedelta(minutes=10))

    assert first.refreshed is True
    assert second.skipped is True
    assert "odds_refresh_skipped_already_fresh" in second.warnings


@patch("football_agent.services.odds_refresh_service.config.USE_COLLECTOR_LAYER", True)
def test_refresh_fail_soft_without_collector_odds(tmp_path: Path) -> None:
    store = OddsRefreshStore(root=tmp_path)
    svc = OddsRefreshService("http://localhost:3000", store=store)
    raw = json.loads((_FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    bundle, trace = MatchCollectorOrchestrator().collect_from_raw(enrich_http_flashscore_raw(raw), MatchRef())
    facts = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(_FIXTURES)).get_facts_for_match(
        "flashscore_sample_league_match",
    )
    collection = MatchCollectionServiceResult(success=True, facts=facts, bundle=bundle, trace=trace)

    with patch(
        "football_agent.services.odds_refresh_service.MatchCollectionService.collect_for_url",
        return_value=collection,
    ):
        result = svc.refresh_for_match_url("https://example.com/match", force=True)

    assert result.success is True
    assert result.refreshed is False
    assert "odds_refresh_failed_no_collector_odds" in result.warnings


@patch("football_agent.services.odds_refresh_service.config.USE_COLLECTOR_LAYER", True)
def test_refresh_marks_previous_snapshot_stale(tmp_path: Path) -> None:
    store = OddsRefreshStore(root=tmp_path)
    svc = OddsRefreshService("http://localhost:3000", store=store)
    collection = _collection_with_odds()
    t1 = datetime(2025, 6, 3, 10, 0, tzinfo=UTC)
    t2 = t1 + timedelta(hours=2)

    with patch(
        "football_agent.services.odds_refresh_service.MatchCollectionService.collect_for_url",
        return_value=collection,
    ):
        svc.refresh_for_match_url("https://example.com/match", force=True, now_utc=t1)
        svc.refresh_for_match_url("https://example.com/match", force=True, now_utc=t2)

    loaded = store.load(collection.bundle.match_key if collection.bundle else "")
    assert loaded.current is not None
    assert loaded.current.refreshed_at_utc == t2
    assert len(loaded.stale) == 1
    assert loaded.stale[0].is_stale is True
