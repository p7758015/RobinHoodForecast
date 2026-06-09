"""Tests for derived league season motivation on top of FlashscoreMatchFacts."""

from __future__ import annotations

from datetime import datetime, timezone

from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.derived_season import LeagueTableMotivationContext, derive_season_motivation
from football_agent.flashscore.models import (
    FlashscoreMatchFacts,
    FlashscoreMeta,
    FlashscoreSeasonContextInputs,
    FlashscoreStandings,
    FlashscoreProvenance,
)


def _facts(
    *,
    matchday: int | None,
    total_matchdays: int | None,
    home_pos: int | None,
    away_pos: int | None,
    home_points: int = 30,
    away_points: int = 28,
    tournament_type: TournamentType = TournamentType.LEAGUE_REGULAR,
) -> FlashscoreMatchFacts:
    meta = FlashscoreMeta(
        match_id="fs-test",
        source_url="https://example.com/match/fs-test",
        competition_name="Test League",
        competition_country="Testland",
        season=2025,
        stage="Regular Season",
        round=str(matchday or ""),
        tournament_type=tournament_type,
        kickoff_utc=datetime(2025, 4, 1, 18, 0, tzinfo=timezone.utc),
        home_team_name="Home FC",
        away_team_name="Away FC",
        status="SCHEDULED",
    )
    standings = FlashscoreStandings(
        home_position=home_pos,
        away_position=away_pos,
        home_points=home_points,
        away_points=away_points,
        home_matches_played=matchday,
        away_matches_played=matchday,
    )
    season_ctx = FlashscoreSeasonContextInputs(
        matchday_number=matchday,
        total_matchdays=total_matchdays,
        rounds_remaining_after_this_match=(total_matchdays - matchday) if matchday and total_matchdays else None,
        table_neighbors={
            "ucl_cutoff_pos": 4,
            "relegation_cutoff_pos": 18,
            "title_leader_points": 33,
            "ucl_cutoff_points": 26,
            "relegation_safety_points": 18,
        },
    )
    provenance = FlashscoreProvenance(scraper_backend_name="test")
    return FlashscoreMatchFacts(
        meta=meta,
        standings=standings,
        season_context_inputs=season_ctx,
        form=None,
        h2h=None,
        squad_raw=None,
        schedule_raw=None,
        stats_raw=None,
        provenance=provenance,
    )


def test_season_phase_early_mid_runin_final() -> None:
    early = derive_season_motivation(_facts(matchday=3, total_matchdays=38, home_pos=5, away_pos=8))
    assert early.season_phase == "EARLY"

    mid = derive_season_motivation(_facts(matchday=15, total_matchdays=38, home_pos=5, away_pos=8))
    assert mid.season_phase == "MID"

    run_in = derive_season_motivation(_facts(matchday=30, total_matchdays=38, home_pos=5, away_pos=8))
    assert run_in.season_phase == "RUN_IN"

    final = derive_season_motivation(_facts(matchday=36, total_matchdays=38, home_pos=5, away_pos=8))
    assert final.season_phase == "FINAL_ROUNDS"


def test_non_league_returns_unknown_phase_and_warning() -> None:
    facts = _facts(
        matchday=10,
        total_matchdays=38,
        home_pos=5,
        away_pos=8,
        tournament_type=TournamentType.DOMESTIC_CUP,
    )
    ctx = derive_season_motivation(facts)
    assert ctx.season_phase == "UNKNOWN"
    assert any("tournament_type" in w for w in ctx.derivation_warnings)


def test_missing_standings_sets_unknown_bands() -> None:
    # Build minimal facts without standings
    meta = FlashscoreMeta(
        match_id="fs-nostand",
        source_url="https://example.com/match/fs-nostand",
        competition_name="Test League",
        competition_country="Testland",
        season=2025,
        stage="Regular Season",
        round="10",
        tournament_type=TournamentType.LEAGUE_REGULAR,
        kickoff_utc=datetime(2025, 3, 1, 18, 0, tzinfo=timezone.utc),
        home_team_name="Home",
        away_team_name="Away",
        status="SCHEDULED",
    )
    season_ctx = FlashscoreSeasonContextInputs(matchday_number=10, total_matchdays=38)
    facts = FlashscoreMatchFacts(
        meta=meta,
        standings=None,
        season_context_inputs=season_ctx,
        form=None,
        h2h=None,
        squad_raw=None,
        schedule_raw=None,
        stats_raw=None,
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )
    ctx = derive_season_motivation(facts)
    assert ctx.home_target_band == "UNKNOWN"
    assert ctx.away_target_band == "UNKNOWN"
    assert any("Standings missing" in w for w in ctx.derivation_warnings)
    assert ctx.gap_to_title_points is None
    assert ctx.home_mathematical_title_alive is None


def test_target_band_and_urgency_simple_cases() -> None:
    # Title contender near top in final rounds
    facts_title = _facts(matchday=36, total_matchdays=38, home_pos=1, away_pos=3)
    ctx_title = derive_season_motivation(facts_title)
    assert ctx_title.home_target_band == "TITLE"
    assert ctx_title.urgency_level_home in ("HIGH", "CRITICAL")

    # Europe race mid-table
    facts_euro = _facts(matchday=30, total_matchdays=38, home_pos=5, away_pos=7)
    ctx_euro = derive_season_motivation(facts_euro)
    assert ctx_euro.home_target_band == "EUROPE"
    assert ctx_euro.urgency_level_home in ("HIGH", "MEDIUM")

    # Relegation battle
    facts_rel = _facts(matchday=30, total_matchdays=38, home_pos=17, away_pos=19)
    ctx_rel = derive_season_motivation(facts_rel)
    assert ctx_rel.home_target_band == "RELEGATION" or ctx_rel.away_target_band == "RELEGATION"
    assert ctx_rel.urgency_level_home in ("HIGH", "MEDIUM", "CRITICAL")


def test_points_thresholds_present_compute_gaps_and_alive() -> None:
    facts = _facts(matchday=14, total_matchdays=38, home_pos=3, away_pos=6, home_points=28, away_points=24)
    ctx = derive_season_motivation(facts)
    assert ctx.gap_to_title_points == 5  # 33 - 28
    assert ctx.gap_to_europe_points == 0  # 26 - 28 -> 0
    assert ctx.gap_to_relegation_safety_points == 0  # 18 - 28 -> 0
    assert ctx.home_mathematical_title_alive is True
    assert ctx.home_mathematical_europe_alive is True
    assert ctx.home_mathematical_relegation_risk_alive is False


def test_missing_points_thresholds_produces_none_and_warnings() -> None:
    facts = _facts(matchday=14, total_matchdays=38, home_pos=3, away_pos=6, home_points=28, away_points=24)
    # Remove thresholds
    facts.season_context_inputs.table_neighbors.pop("title_leader_points", None)
    facts.season_context_inputs.table_neighbors.pop("ucl_cutoff_points", None)
    facts.season_context_inputs.table_neighbors.pop("relegation_safety_points", None)
    ctx = derive_season_motivation(facts)
    assert ctx.gap_to_title_points is None
    assert ctx.gap_to_europe_points is None
    assert ctx.gap_to_relegation_safety_points is None
    assert ctx.home_mathematical_title_alive is None
    assert ctx.home_mathematical_europe_alive is None
    assert ctx.home_mathematical_relegation_risk_alive is None
    assert "missing title_leader_points" in ctx.derivation_warnings
    assert "missing ucl_cutoff_points" in ctx.derivation_warnings
    assert "missing relegation_safety_points" in ctx.derivation_warnings

