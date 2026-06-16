"""Coach block + live odds pipeline enrichment tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.collectors.odds_bridge import (
    align_odds_meta_to_facts,
    build_odds_bundle_from_flashscore_raw,
    resolve_pipeline_odds_context,
)
from football_agent.domain.enums_v2 import CoachTenurePhase, TournamentType
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.raw_enrich import enrich_http_flashscore_raw
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.merge.news_merge import merge_news_into_merged_context
from football_agent.news_context.models import CoachContextBlock, MatchNewsContext
from football_agent.normalizers.coach_snapshot_helpers import coach_context_from_merged
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.domain.models_v2 import TeamRefV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.services.scoring_service_v2 import ScoringServiceV2

FIXTURES = Path(__file__).parent / "data"


def _facts(stem: str = "flashscore_sample_league_match"):
    return FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES)).get_facts_for_match(stem)


def _odds(stem: str = "odds_sample"):
    return OddsIngestionService(FixtureFileOddsAdapter(FIXTURES)).get_odds_for_fixture(stem)


def test_coach_name_and_tenure_from_brave() -> None:
    facts = _facts()
    assert facts is not None
    if facts.squad_raw:
        facts = facts.model_copy(
            update={
                "squad_raw": facts.squad_raw.model_copy(
                    update={"coach_name_home": "Unknown", "coach_name_away": "Unknown"},
                ),
            },
        )
    team = TeamRefV2(team_id=1, name="AC Milan")
    news = MatchNewsContext(
        source_count=3,
        confidence=0.62,
        coach=CoachContextBlock(
            home_coach_name="Brave Coach",
            away_coach_name="Away Coach",
            home_coach_tenure_days=8,
            away_coach_tenure_days=120,
            coach_news_confidence=0.58,
            home_coach_recent_quotes=['"This is my first game in charge"'],
        ),
    )
    merged = merge_news_into_merged_context(
        merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None),
        news,
    )
    home = coach_context_from_merged(merged, team, side="home")
    assert home.coach.name == "Brave Coach"
    assert home.days_in_charge == 8
    assert home.is_first_match is True
    assert home.tenure_phase == CoachTenurePhase.FIRST_MATCH
    assert home.coach_global_strength_score != 0.5 or home.matches_in_charge is not None


def test_coach_bounce_window_from_tenure_days() -> None:
    facts = _facts()
    assert facts is not None
    if facts.squad_raw:
        facts = facts.model_copy(
            update={
                "squad_raw": facts.squad_raw.model_copy(
                    update={"coach_name_home": "Unknown", "coach_name_away": "Unknown"},
                ),
            },
        )
    team = TeamRefV2(team_id=2, name="Juventus")
    news = MatchNewsContext(
        source_count=2,
        confidence=0.5,
        coach=CoachContextBlock(
            away_coach_name="Tenured Coach",
            away_coach_tenure_days=21,
            coach_news_confidence=0.45,
        ),
    )
    merged = merge_news_into_merged_context(
        merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None),
        news,
    )
    away = coach_context_from_merged(merged, team, side="away")
    assert away.coach.name == "Tenured Coach"
    assert away.is_new_coach_bounce_window is True
    assert away.tenure_phase == CoachTenurePhase.BOUNCE_WINDOW


def test_coaches_confidence_not_flat_with_rich_context() -> None:
    facts = _facts()
    odds = _odds()
    assert facts is not None and odds is not None
    news = MatchNewsContext(
        source_count=2,
        confidence=0.55,
        coach=CoachContextBlock(
            home_coach_name="Coach Home",
            away_coach_name="Coach Away",
            home_coach_tenure_days=45,
            away_coach_tenure_days=200,
            coach_news_confidence=0.52,
        ),
    )
    merged = merge_news_into_merged_context(
        merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds),
        news,
    )
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.confidence.coaches_confidence >= 0.5
    assert snap.home_coach.matches_in_charge is not None


def test_sparse_coach_data_low_confidence_fail_soft() -> None:
    facts = _facts()
    assert facts is not None
    if facts.squad_raw:
        facts = facts.model_copy(
            update={
                "squad_raw": facts.squad_raw.model_copy(
                    update={"coach_name_home": "Unknown", "coach_name_away": "Unknown"},
                ),
            },
        )
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.home_coach.coach.name in ("Unknown", "")
    assert snap.confidence.coaches_confidence <= 0.25


def test_live_odds_enrichment_path_reaches_scorer() -> None:
    facts = _facts()
    odds = _odds()
    assert facts is not None and odds is not None
    aligned = align_odds_meta_to_facts(facts, odds)
    ctx, _, source = resolve_pipeline_odds_context(
        facts=facts,
        collector_bundle=None,
        enrichment_odds=aligned,
    )
    assert source == "enrichment"
    assert ctx is not None and ctx.coverage is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=ctx)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snap, report)
    assert scored.prediction.best_market.book_odds is not None


def test_embedded_flashscore_odds_bundle_maps_to_snapshot() -> None:
    raw = json.loads((FIXTURES / "flashscore_sample_league_match.json").read_text(encoding="utf-8"))
    raw = enrich_http_flashscore_raw(raw)
    raw["odds"] = {
        "bookmaker_name": "Flashscore",
        "markets": {
            "home_win": {"value": 1.95, "raw_label": "1"},
            "away_win": {"value": 3.8, "raw_label": "2"},
            "btts_yes": {"value": 1.75, "raw_label": "Yes"},
        },
    }
    facts = _facts()
    assert facts is not None
    bundle = build_odds_bundle_from_flashscore_raw(raw, match_key=facts.meta.match_id)
    assert bundle is not None
    ctx, _, source = resolve_pipeline_odds_context(
        facts=facts,
        collector_bundle=bundle,
        enrichment_odds=None,
    )
    assert source == "collector"
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=ctx)
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.odds.home_win is not None
    assert snap.odds.btts_yes is not None


def test_parked_path_unchanged_with_coach_odds_enrichment() -> None:
    facts = _facts()
    assert facts is not None
    cup = facts.model_copy(update={"meta": facts.meta.model_copy(update={"tournament_type": TournamentType.DOMESTIC_CUP})})
    odds = _odds()
    news = MatchNewsContext(
        coach=CoachContextBlock(home_coach_name="Cup Coach", coach_news_confidence=0.4),
        source_count=1,
        confidence=0.4,
    )
    merged = merge_news_into_merged_context(
        merge_match_context_v2(facts=cup, openclaw_context=None, odds_context=odds),
        news,
    )
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snap, report)
    assert scored.prediction.analysis_mode == "analysis_only"
    assert scored.prediction.best_market is None


def test_pipeline_always_uses_odds_bridge_for_enrichment(monkeypatch) -> None:
    from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline

    facts = _facts()
    odds = _odds()
    assert facts is not None and odds is not None

    calls: list = []

    def _fake_resolve(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return odds, ["odds_bridge:test"], "enrichment"

    monkeypatch.setattr(
        "football_agent.services.live_flashscore_pipeline.resolve_pipeline_odds_context",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts",
        lambda *a, **k: MagicMock(
            context=None,
            odds=odds,
            news=None,
            sources={"odds": "ok"},
            warnings=[],
            routing=MagicMock(enrichment_mode="split", odds_source="separate"),
        ),
    )
    monkeypatch.setattr("football_agent.config.USE_COLLECTOR_LAYER", False)
    monkeypatch.setattr(
        LiveFlashscorePipeline,
        "_fetch_facts",
        lambda self, *a, **k: (facts, {"flashscore": "ok"}, None, None),
    )
    monkeypatch.setattr(
        LiveFlashscorePipeline,
        "_fetch_facts_collector",
        lambda self, *a, **k: (facts, {"flashscore": "ok"}, None, [], None),
    )

    pipe = LiveFlashscorePipeline(
        scraper_url="http://example.invalid",
        persist=False,
        skip_openclaw=True,
    )
    result = pipe.analyze_teams("AC Milan", "Juventus", "2025-11-29")
    assert result.success
    assert calls
    assert calls[0]["enrichment_odds"] is odds
