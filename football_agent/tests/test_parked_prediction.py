"""Parked / analysis-only prediction semantics."""

from __future__ import annotations

from unittest.mock import MagicMock

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.output.v2_user_output import build_match_payload_from_result, format_v2_single_match_text
from football_agent.scorers.parked_prediction import build_parked_prediction
from football_agent.scorers.routing import ScorerRoutingDecision, resolve_scorer_route
from football_agent.services.competition_classifier import CompetitionClassification
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoringServiceV2, ScoredRunV2
from football_agent.normalizers.merged_snapshot_builder_v2 import BuildReport
from football_agent.tests.test_scorer_v2 import make_snapshot


def _decision(category: CompetitionContextClass) -> ScorerRoutingDecision:
    return resolve_scorer_route(
        CompetitionClassification(
            category=category,
            tournament_type={
                CompetitionContextClass.DOMESTIC_CUP: TournamentType.DOMESTIC_CUP,
                CompetitionContextClass.INTERNATIONAL_CLUB: TournamentType.INTERNATIONAL_CLUB,
                CompetitionContextClass.NATIONAL_TEAM: TournamentType.INTERNATIONAL_NATIONAL,
                CompetitionContextClass.FRIENDLY: TournamentType.FRIENDLY,
                CompetitionContextClass.UNKNOWN: TournamentType.UNKNOWN,
            }[category],
            confidence="high",
            signals=["test"],
        ),
    )


def test_parked_prediction_has_structured_metadata() -> None:
    snap = make_snapshot(with_odds=True)
    decision = _decision(CompetitionContextClass.DOMESTIC_CUP)
    pred = build_parked_prediction(snap, decision)

    assert pred.analysis_mode == "analysis_only"
    assert pred.prediction_mode == "parked_analysis_only"
    assert pred.best_market is None
    assert pred.market_predictions == []
    assert pred.parked_context is not None
    assert pred.parked_context.reason == "parked:domestic_cup"
    assert pred.parked_context.route == "non_league_parked"
    assert pred.parked_context.can_build_express is False
    assert pred.parked_context.book_odds_available is True
    assert pred.parked_context.book_odds_markets_count >= 1
    assert pred.prediction_summary
    assert "analysis-only" in pred.prediction_summary.lower() or "League scoring" in pred.prediction_summary
    assert pred.overall_confidence_score > 0.0
    assert "analysis_only_mode" in pred.express_safety.reasons


def test_parked_summary_varies_by_tournament_type() -> None:
    snap = make_snapshot(with_odds=False)
    cup = build_parked_prediction(snap, _decision(CompetitionContextClass.DOMESTIC_CUP))
    intl = build_parked_prediction(snap, _decision(CompetitionContextClass.INTERNATIONAL_CLUB))
    nat = build_parked_prediction(snap, _decision(CompetitionContextClass.NATIONAL_TEAM))
    friendly = build_parked_prediction(snap, _decision(CompetitionContextClass.FRIENDLY))
    unknown = build_parked_prediction(
        snap,
        resolve_scorer_route(
            CompetitionClassification(
                category=CompetitionContextClass.UNKNOWN,
                tournament_type=TournamentType.UNKNOWN,
                confidence="low",
                signals=[],
            ),
        ),
    )

    assert cup.parked_context.reason == "parked:domestic_cup"
    assert intl.parked_context.reason == "parked:international_club"
    assert nat.parked_context.reason == "parked:national_team"
    assert friendly.parked_context.reason == "parked:friendly"
    assert unknown.parked_context.route == "unknown_parked"
    assert unknown.parked_context.reason == "parked:unknown"
    assert cup.prediction_summary != intl.prediction_summary


def test_scoring_service_parked_does_not_call_league_scorer() -> None:
    snap = make_snapshot(with_odds=True)
    mock_scorer = MagicMock()
    svc = ScoringServiceV2(scorer=mock_scorer)
    clf = CompetitionClassification(
        category=CompetitionContextClass.DOMESTIC_CUP,
        tournament_type=TournamentType.DOMESTIC_CUP,
        confidence="high",
        signals=[],
    )
    scored = svc.score_snapshot_with_report(snap, BuildReport(), classification=clf)
    mock_scorer.score_snapshot.assert_not_called()
    assert scored.prediction.analysis_mode == "analysis_only"
    assert scored.prediction.parked_context is not None
    assert scored.prediction.parked_context.book_odds_available


def test_parked_payload_and_telegram_output() -> None:
    snap = make_snapshot(with_odds=True)
    pred = build_parked_prediction(snap, _decision(CompetitionContextClass.DOMESTIC_CUP))
    payload = build_match_payload_from_result(pred)
    text = format_v2_single_match_text(payload)

    assert payload["analysis_mode"] == "analysis_only"
    assert payload["best_market"] is None
    assert payload["parked_context"] is not None
    assert "analysis-only" in text

    report = MagicMock()
    report.merge_missing_blocks = []
    report.merge_warnings = []
    scored = ScoredRunV2(
        snapshot=snap,
        prediction=pred,
        build_report=report,
        scoring_skipped=True,
        scorer_name="routing_parked",
    )
    tg = format_telegram_match_reply(
        LivePipelineResult(success=True, path="flashscore_url", scored_run=scored),
    )
    assert "analysis-only" in tg
    assert "Лучший рынок" not in tg
    assert "кодовый прогноз" in tg.lower() or "Кодовый прогноз" in tg


def test_league_scored_prediction_keeps_default_modes() -> None:
    snap = make_snapshot(with_odds=False)
    clf = CompetitionClassification(
        category=CompetitionContextClass.LEAGUE,
        tournament_type=TournamentType.LEAGUE_REGULAR,
        confidence="high",
        signals=[],
    )
    scored = ScoringServiceV2().score_snapshot_with_report(snap, BuildReport(), classification=clf)
    assert scored.prediction.analysis_mode == "full_scoring"
    assert scored.prediction.prediction_mode == "league_scored"
    assert scored.prediction.parked_context is None
