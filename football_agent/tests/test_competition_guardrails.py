"""Competition guardrail tests."""

from __future__ import annotations

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.services.competition_classifier import CompetitionClassification
from football_agent.services.competition_guardrails import apply_competition_guardrails
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.tests.test_scorer_v2 import make_snapshot
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from unittest.mock import MagicMock


def _classification(category: CompetitionContextClass) -> CompetitionClassification:
    return CompetitionClassification(
        category=category,
        tournament_type=TournamentType.DOMESTIC_CUP,
        confidence="high",
        signals=["test"],
    )


def test_league_no_guardrail() -> None:
    snap = make_snapshot(with_odds=False)
    pred = LeagueScorerV2().score_snapshot(snap)
    scored = ScoredRunV2(snapshot=snap, prediction=pred, build_report=MagicMock())
    clf = CompetitionClassification(
        category=CompetitionContextClass.LEAGUE,
        tournament_type=TournamentType.LEAGUE_REGULAR,
        confidence="high",
        signals=[],
    )
    out, guard = apply_competition_guardrails(scored, clf)
    assert guard.guardrail_applied is False
    assert out.prediction.overall_confidence_score == pred.overall_confidence_score


def test_cup_lowers_confidence() -> None:
    snap = make_snapshot(with_odds=False, confidence=0.70)
    pred = LeagueScorerV2().score_snapshot(snap)
    scored = ScoredRunV2(snapshot=snap, prediction=pred, build_report=MagicMock())
    out, guard = apply_competition_guardrails(scored, _classification(CompetitionContextClass.DOMESTIC_CUP))
    assert guard.guardrail_applied is True
    assert out.prediction.overall_confidence_score < pred.overall_confidence_score
    assert guard.confidence_penalty == 0.08


def test_national_team_stronger_penalty() -> None:
    snap = make_snapshot(with_odds=False, confidence=0.65)
    pred = LeagueScorerV2().score_snapshot(snap)
    scored = ScoredRunV2(snapshot=snap, prediction=pred, build_report=MagicMock())
    _, guard = apply_competition_guardrails(
        scored,
        _classification(CompetitionContextClass.NATIONAL_TEAM),
    )
    assert guard.confidence_penalty == 0.12
    assert "national_team" in guard.warnings[0]


def test_telegram_shows_cup_context() -> None:
    snap = make_snapshot(with_odds=False)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = []
    report.merge_warnings = []
    report.odds_link_strategy = "none"
    report.openclaw_link_strategy = "none"
    scored = ScoredRunV2(snapshot=snap, prediction=pred, build_report=report)
    clf = _classification(CompetitionContextClass.DOMESTIC_CUP)
    scored, guard = apply_competition_guardrails(scored, clf)
    result = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=scored,
        sources={"flashscore": "ok"},
        competition_classification=clf,
        competition_guardrail=guard,
    )
    text = format_telegram_match_reply(result)
    assert "кубок" in text.lower() or "кубок" in (guard.telegram_hint or "")
