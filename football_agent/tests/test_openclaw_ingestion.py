"""OpenClaw ingestion: raw payload → snapshot → scorer."""

from __future__ import annotations

from datetime import date, datetime, timezone

from football_agent.domain.enums_v2 import NewsSeverity, TournamentType
from football_agent.openclaw.adapter import OpenClawSnapshotBuilder
from football_agent.openclaw.models import (
    OpenClawCoachBlock,
    OpenClawH2HBlock,
    OpenClawMatchMeta,
    OpenClawMatchPayload,
    OpenClawNewsBlock,
    OpenClawNewsItem,
    OpenClawOddsMarkets,
    OpenClawPlayerAvailability,
    OpenClawPlayerRef,
    OpenClawScheduleBlock,
    OpenClawScheduleMatchStub,
    OpenClawSourceMetadata,
    OpenClawSquadBlock,
    OpenClawTeamContextBlock,
    OpenClawTeamRef,
    OpenClawTableContext,
)
from football_agent.openclaw.service import OpenClawLeagueAnalysisService
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2

UTC = timezone.utc


def _full_payload() -> OpenClawMatchPayload:
    dt = datetime(2025, 11, 29, 17, 30, tzinfo=UTC)
    return OpenClawMatchPayload(
        source=OpenClawSourceMetadata(
            source_name="test-openclaw",
            data_freshness_score=0.85,
            completeness_score=0.7,
            confidence_score=0.65,
            tags=["smoke"],
        ),
        meta=OpenClawMatchMeta(
            internal_match_id_hint=9001,
            season=2025,
            match_date_utc=dt,
            competition_name="Serie A",
            competition_code="SA",
            tournament_type=TournamentType.LEAGUE_REGULAR,
            round_number=14,
            country="Italy",
        ),
        home_team=OpenClawTeamRef(team_id=101, name="AC Milan", short_name="Milan"),
        away_team=OpenClawTeamRef(team_id=102, name="Juventus FC", short_name="Juve"),
        home_context=OpenClawTeamContextBlock(
            team=OpenClawTeamRef(team_id=101, name="AC Milan"),
            baseline_strength_score=0.62,
            motivation_score=0.55,
            availability_score=0.7,
            bench_quality_score=0.58,
            line_stability_score=0.72,
            table=OpenClawTableContext(position=3, points=28, form_string="WWDLW"),
            mini_schedule=None,
        ),
        away_context=OpenClawTeamContextBlock(
            team=OpenClawTeamRef(team_id=102, name="Juventus FC"),
            baseline_strength_score=0.6,
            motivation_score=0.52,
            table=OpenClawTableContext(position=6, points=24, form_string="DLWWD"),
        ),
        home_squad=OpenClawSquadBlock(
            expected_starting_xi=[
                OpenClawPlayerRef(player_id=1, name="Maignan", position="G"),
            ],
            unavailable=[
                OpenClawPlayerAvailability(
                    player=OpenClawPlayerRef(player_id=99, name="Starter X"),
                    status="INJURED",
                    importance="HIGH",
                ),
            ],
            doubtful=[],
            suspended=[],
            starting_xi_confidence=0.74,
        ),
        away_squad=None,
        home_coach=OpenClawCoachBlock(
            name="Fonseca",
            source_quality_score=0.71,
            coach_global_strength_score=0.55,
        ),
        away_coach=OpenClawCoachBlock(
            name="Motta",
            source_quality_score=0.68,
        ),
        home_schedule=OpenClawScheduleBlock(
            days_since_last_match=5,
            days_to_next_match=4,
            matches_last_14_days=4,
            prev_match=OpenClawScheduleMatchStub(
                competition_code="SA",
                competition_name="Serie A",
                opponent_name="Torino",
                is_home=True,
                match_date=date(2025, 11, 23),
            ),
        ),
        odds=OpenClawOddsMarkets(
            home_win=2.05,
            draw=3.35,
            away_win=3.6,
            home_not_lose=1.32,
            away_not_lose=1.82,
            btts_yes=1.75,
            home_team_to_score=1.42,
            away_team_to_score=1.55,
            over_15=1.24,
            bookmaker="TestBook",
        ),
        h2h=OpenClawH2HBlock(
            team_h2h_total_matches=10,
            team_h2h_recent_score=0.1,
            team_h2h_home_away_split=-0.05,
            h2h_btts_rate=0.52,
            h2h_over25_rate=0.48,
            h2h_context_bias=0.02,
        ),
        news=OpenClawNewsBlock(
            items=[
                OpenClawNewsItem(
                    title="Key return from injury",
                    severity=NewsSeverity.LOW,
                    relevance_score=0.5,
                ),
            ],
            priority_signals=["fitness ok"],
        ),
    )


def test_openclaw_payload_to_valid_snapshot() -> None:
    snap = OpenClawSnapshotBuilder().build(_full_payload())
    assert snap.match_meta.competition_code == "SA"
    assert snap.home_team_context.team.name == "AC Milan"
    assert snap.odds.home_win is not None
    assert snap.odds.home_win.odds == 2.05


def test_partial_payload_still_builds() -> None:
    p = OpenClawMatchPayload(meta=None, home_team=OpenClawTeamRef(name="A"), away_team=OpenClawTeamRef(name="B"))
    snap = OpenClawSnapshotBuilder().build(p)
    assert snap.match_meta.home_team.name == "A"
    assert snap.h2h_context.team_h2h_total_matches == 0


def test_odds_reach_snapshot() -> None:
    p = OpenClawMatchPayload(
        meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 1, 1, 12, tzinfo=UTC)),
        home_team=OpenClawTeamRef(name="Team A"),
        away_team=OpenClawTeamRef(name="Team B"),
        odds=OpenClawOddsMarkets(away_win=4.2, btts_yes=1.61),
    )
    snap = OpenClawSnapshotBuilder().build(p)
    assert snap.odds.away_win is not None
    assert snap.odds.btts_yes is not None
    assert snap.odds.over_15 is None


def test_news_injuries_do_not_break_builder() -> None:
    p = OpenClawMatchPayload(
        meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 1, 1, 12, tzinfo=UTC)),
        home_team=OpenClawTeamRef(name="Team A"),
        away_team=OpenClawTeamRef(name="Team B"),
        home_squad=OpenClawSquadBlock(
            unavailable=[
                OpenClawPlayerAvailability(
                    player=OpenClawPlayerRef(name="P1"),
                    status="SUSPENDED",
                    importance="CRITICAL",
                ),
            ],
        ),
        news=OpenClawNewsBlock(locker_room_issues=["rumours"], news_risk_score=0.33),
    )
    snap = OpenClawSnapshotBuilder().build(p)
    assert len(snap.news_context.locker_room_issues) == 1
    assert snap.home_squad.missing_key_players_count >= 1


def test_scorer_accepts_openclaw_snapshot() -> None:
    snap = OpenClawSnapshotBuilder().build(_full_payload())
    result = LeagueScorerV2().score_snapshot(snap)
    assert result.best_market is not None
    assert result.match_meta.match_id == 9001


def test_service_end_to_end_in_memory() -> None:
    svc = OpenClawLeagueAnalysisService()
    r = svc.analyze_from_payload(_full_payload())
    assert r.overall_confidence_score >= 0
    assert len(r.market_predictions) >= 5
