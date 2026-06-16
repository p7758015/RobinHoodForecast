"""Scoring service + live pipeline scorer routing integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
from football_agent.services.competition_classifier import (
    CompetitionClassification,
    classify_competition_from_facts,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.tests.test_scorer_v2 import make_snapshot

FIXTURES = Path(__file__).parent / "data"


def _league_classification() -> CompetitionClassification:
    return CompetitionClassification(
        category=CompetitionContextClass.LEAGUE,
        tournament_type=TournamentType.LEAGUE_REGULAR,
        confidence="high",
        signals=["test"],
    )


def _cup_classification() -> CompetitionClassification:
    return CompetitionClassification(
        category=CompetitionContextClass.DOMESTIC_CUP,
        tournament_type=TournamentType.DOMESTIC_CUP,
        confidence="high",
        signals=["test"],
    )


def test_scoring_service_league_route_calls_scorer() -> None:
    snapshot = make_snapshot(with_odds=False)
    report = BuildReport()
    from football_agent.scorers.league_scorer_v2 import LeagueScorerV2

    expected = LeagueScorerV2().score_snapshot(snapshot)
    mock_scorer = MagicMock()
    mock_scorer.score_snapshot.return_value = expected
    svc = ScoringServiceV2(scorer=mock_scorer)
    scored = svc.score_snapshot_with_report(
        snapshot,
        report,
        classification=_league_classification(),
    )
    mock_scorer.score_snapshot.assert_called_once_with(snapshot)
    assert scored.routing_decision is not None
    assert scored.routing_decision.route == "league_full"
    assert scored.scoring_skipped is False
    assert scored.prediction.best_market is not None


def test_scoring_service_parked_skips_league_scorer() -> None:
    snapshot = make_snapshot(with_odds=False)
    report = BuildReport()
    mock_scorer = MagicMock()
    svc = ScoringServiceV2(scorer=mock_scorer)
    scored = svc.score_snapshot_with_report(
        snapshot,
        report,
        classification=_cup_classification(),
    )
    mock_scorer.score_snapshot.assert_not_called()
    assert scored.routing_decision is not None
    assert scored.routing_decision.route == "non_league_parked"
    assert scored.scoring_skipped is True
    assert scored.prediction.best_market is None
    assert scored.prediction.analysis_mode == "analysis_only"
    assert scored.prediction.parked_context is not None
    assert scored.prediction.parked_context.reason == "parked:domestic_cup"
    assert scored.prediction.prediction_summary
    assert scored.scorer_name == "routing_parked"
    assert "parked:domestic_cup" in scored.scoring_warnings


def _cup_facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="cup1",
            source_url="https://example.com",
            competition_name="FA Cup",
            home_team_name="Arsenal",
            away_team_name="Chelsea",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", False)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_live_pipeline_league_routing(mock_enrich, mock_fs, _url) -> None:
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(FIXTURES),
    ).get_facts_for_match("flashscore_botola_sample_match")
    assert facts is not None
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = EnrichmentFetchResult(
        sources={"openclaw": "skipped", "odds": "skipped"},
        warnings=[],
    )

    result = LiveFlashscorePipeline(persist=False, skip_openclaw=True).analyze_flashscore_url(
        "https://example.com/m",
    )

    assert result.success is True
    assert result.routing_decision is not None
    assert result.routing_decision.route == "league_full"
    assert result.scored_run is not None
    assert result.scored_run.scoring_skipped is False
    assert any("scorer_route:league_full" in w for w in result.warnings)


@patch("football_agent.services.live_flashscore_pipeline.config.USE_COLLECTOR_LAYER", False)
@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_live_pipeline_cup_parked_routing(mock_enrich, mock_fs, _url) -> None:
    facts = _cup_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = EnrichmentFetchResult(
        sources={"openclaw": "skipped", "odds": "skipped"},
        warnings=[],
    )

    result = LiveFlashscorePipeline(persist=False, skip_openclaw=True).analyze_flashscore_url(
        "https://example.com/m",
    )

    assert result.success is True
    assert result.routing_decision is not None
    assert result.routing_decision.route == "non_league_parked"
    assert result.scored_run is not None
    assert result.scored_run.scoring_skipped is True
    assert result.scored_run.prediction.analysis_mode == "analysis_only"
    assert result.scored_run.prediction.parked_context is not None
    assert result.scored_run.prediction.prediction_summary
    assert result.scored_run.prediction.best_market is None
    assert any("scorer_route:non_league_parked" in w for w in result.warnings)


def test_classify_fa_cup_not_league_eligible() -> None:
    clf = classify_competition_from_facts(_cup_facts())
    assert clf.is_league_eligible is False
