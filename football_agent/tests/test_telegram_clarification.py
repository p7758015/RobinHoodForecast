"""Tests for Telegram clarification flow and league reply formatting."""

from __future__ import annotations

from unittest.mock import MagicMock

from football_agent.bot.clarification_flow import merge_clarification_text
from football_agent.bot.clarification_state import ClarificationStateStore, PendingClarification
from football_agent.bot.request_parser import (
    ClarificationReason,
    MatchRequestKind,
    parse_match_request,
)
from football_agent.discovery.models import CompetitionCandidate, CompetitionResolveResult
from football_agent.output.telegram_league_output import format_teleague_league_reply
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.services.scoring_service_v2 import ScoredRunV2
from football_agent.services.telegram_league_analysis_service import TelegramLeagueAnalysisService
from football_agent.services.telegram_match_analysis_service import TelegramMatchAnalysisService
from football_agent.tests.test_competition_discovery import _mock_search
from football_agent.tests.test_scorer_v2 import make_snapshot


def _scored_run_stub(*, parked: bool = False) -> ScoredRunV2:
    snap = make_snapshot(with_odds=True)
    pred = LeagueScorerV2().score_snapshot(snap)
    if parked:
        pred.analysis_mode = "analysis_only"
    report = MagicMock()
    report.merge_missing_blocks = []
    report.merge_warnings = []
    report.odds_link_strategy = "by_match_id"
    report.openclaw_link_strategy = "none"
    return ScoredRunV2(snapshot=snap, prediction=pred, build_report=report)


def test_vague_query_returns_clarification() -> None:
    req = parse_match_request("дай прогноз")
    assert req.kind == MatchRequestKind.NEEDS_CLARIFICATION
    assert req.clarification_reason == ClarificationReason.TOO_VAGUE

    svc = TelegramMatchAnalysisService(pipeline=MagicMock())
    resp = svc.analyze_text("дай прогноз", chat_id=42)
    assert resp.needs_clarification is True
    assert "Примеры" in resp.reply_text or "примеры" in resp.reply_text.lower()


def test_missing_league_shell_clarification() -> None:
    req = parse_match_request("дай прогноз на лигу")
    assert req.kind == MatchRequestKind.NEEDS_CLARIFICATION
    assert req.clarification_reason == ClarificationReason.MISSING_LEAGUE


def test_single_team_needs_opponent() -> None:
    req = parse_match_request("прогноз на барсу")
    assert req.kind == MatchRequestKind.NEEDS_CLARIFICATION
    assert req.clarification_reason == ClarificationReason.MISSING_OPPONENT
    assert req.partial_home


def test_ambiguous_teams_without_separator() -> None:
    req = parse_match_request("арсенал челси")
    assert req.kind == MatchRequestKind.NEEDS_CLARIFICATION
    assert req.clarification_reason == ClarificationReason.AMBIGUOUS_TEAMS


def test_date_ambiguity_clarification() -> None:
    req = parse_match_request("дай прогноз на лигу Китая на выходных")
    assert req.kind == MatchRequestKind.NEEDS_CLARIFICATION
    assert req.clarification_reason == ClarificationReason.DATE_AMBIGUOUS


def test_clarification_followup_league() -> None:
    store = ClarificationStateStore(ttl_s=600)
    svc = TelegramMatchAnalysisService(
        pipeline=MagicMock(),
        clarification_store=store,
        league_service=_league_service_with_mocks(),
    )
    r1 = svc.analyze_text("дай прогноз", chat_id=7)
    assert r1.needs_clarification

    r2 = svc.analyze_text("на лигу Китая", chat_id=7)
    assert r2.request_kind == "league_query"
    assert r2.success is True


def test_clarification_followup_match_opponent() -> None:
    merged = merge_clarification_text(
        PendingClarification(
            chat_id=1,
            intent="match",
            reason=ClarificationReason.MISSING_OPPONENT,
            partial_home="Интер",
        ),
        "с Миланом",
    )
    req = parse_match_request(merged)
    assert req.kind == MatchRequestKind.TEAM_QUERY
    assert req.home_team
    assert req.away_team


def test_ambiguous_league_resolver_clarification() -> None:
    resolver = MagicMock()
    resolver.resolve_competition.return_value = CompetitionResolveResult(
        query="china",
        candidates=[
            CompetitionCandidate("A", "China", "http://a", source="scraper_search"),
            CompetitionCandidate("B", "China", "http://b", source="scraper_search"),
        ],
        resolved=None,
        ambiguous=True,
        warnings=["ambiguous_competition_query"],
    )
    svc = TelegramLeagueAnalysisService(resolver=resolver)
    req = parse_match_request("дай прогноз на лигу китая")
    resp = svc.analyze_league_request(req)
    assert resp.needs_clarification is True
    assert "несколько" in resp.reply_text.lower() or "уточните" in resp.reply_text.lower()


def test_league_reply_structured_format() -> None:
    result = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(),
        sources={"flashscore": "ok", "odds": "ok"},
    )
    parked = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=_scored_run_stub(parked=True),
        sources={"flashscore": "ok"},
    )
    text = format_teleague_league_reply(
        competition_name="Chinese Super League",
        competition_country="China",
        date_from="2026-06-03",
        date_to="2026-06-09",
        results=[result, parked],
        period_note="дата не указана — ближайшие 7 дней",
    )
    assert "Лига: Chinese Super League" in text
    assert "Период:" in text
    assert "Тип: league prediction" in text
    assert "Тип: analysis-only" in text
    assert "Итого:" in text
    assert "Риски по выборке" in text


def test_url_and_team_pair_still_work() -> None:
    assert parse_match_request(
        "https://www.flashscore.com/match/football/a/b/?mid=x",
    ).kind == MatchRequestKind.FLASHSCORE_URL
    assert parse_match_request("Real Madrid — Barcelona").kind == MatchRequestKind.TEAM_QUERY
    assert parse_match_request("дай прогноз на лигу Китая").kind == MatchRequestKind.LEAGUE_QUERY


def _league_service_with_mocks() -> TelegramLeagueAnalysisService:
    from football_agent.discovery.competition_resolver import CompetitionResolverService
    from football_agent.discovery.fixture_discovery import FixtureDiscoveryService

    resolver = CompetitionResolverService(search_fn=_mock_search, enable_brave_normalize=False)
    fixture_svc = FixtureDiscoveryService(
        resolver=resolver,
        fixtures_fn=lambda url, d_from, d_to: [
            {
                "match_id": "m1",
                "home_team_name": "Home",
                "away_team_name": "Away",
                "source_url": "https://flashscore.com/match/m1",
                "date": d_from,
            }
        ],
    )
    return TelegramLeagueAnalysisService(
        resolver=resolver,
        fixture_svc=fixture_svc,
        pipeline_runner=lambda _u: LivePipelineResult(
            success=True,
            path="flashscore_url",
            scored_run=_scored_run_stub(),
        ),
    )
