"""League snapshot enrichment via MergedSnapshotBuilderV2 (form, motivation, schedule, confidence)."""

from __future__ import annotations

from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.domain.enums_v2 import MotivationContext, TournamentType
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.derived_season import derive_season_motivation
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.news_context.models import CoachContextBlock, GeneralNewsBlock, MatchNewsContext
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService

FIXTURES_DIR = Path(__file__).parent / "data"


def _facts(fixture_name: str = "flashscore_sample_league_match"):
    svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))
    return svc.get_facts_for_match(fixture_name)


def _odds():
    svc = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES_DIR))
    return svc.get_odds_for_fixture("odds_sample")


def _build(fixture_name: str = "flashscore_sample_league_match", *, news=None):
    facts = _facts(fixture_name)
    odds = _odds()
    assert facts is not None and odds is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds, news_context=news)
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)
    return snap, merged


def test_league_team_blocks_populated_from_facts() -> None:
    snap, _ = _build()

    home_form = snap.home_team_context.form
    assert home_form.last_5_form_score > 0.5
    assert home_form.last_10_form_score > 0.5

    home_mot = snap.home_team_context.motivation
    assert home_mot.league_position == 3
    assert home_mot.points == 28
    assert home_mot.goal_difference == 12
    assert home_mot.motivation_context in (
        MotivationContext.TITLE_RACE,
        MotivationContext.EURO_RACE,
        MotivationContext.MIDTABLE_NEUTRAL,
    )
    assert home_mot.motivation_score != 0.5 or home_mot.motivation_context is not None

    away_mot = snap.away_team_context.motivation
    assert away_mot.league_position == 6
    assert away_mot.points == 24


def test_confidence_not_entirely_flat_defaults() -> None:
    snap, _ = _build()
    conf = snap.confidence

    assert conf.teams_confidence >= 0.6
    assert conf.h2h_confidence >= 0.5
    assert conf.schedule_confidence >= 0.35
    assert conf.overall_completeness_score != 0.5
    assert conf.overall_confidence_score != 0.5

    block_values = [
        conf.match_meta_confidence,
        conf.teams_confidence,
        conf.squads_confidence,
        conf.coaches_confidence,
        conf.odds_confidence,
        conf.h2h_confidence,
        conf.schedule_confidence,
    ]
    assert max(block_values) - min(block_values) >= 0.15


def test_schedule_block_partially_filled() -> None:
    snap, _ = _build()
    sched = snap.home_schedule

    assert sched.days_since_last_match == 5
    assert sched.matches_last_14_days >= 2
    assert sched.fixture_congestion_score > 0.0
    assert sched.team.name == "AC Milan"


def test_squads_coaches_preserve_available_signals() -> None:
    snap, _ = _build()

    assert snap.home_coach.coach.name == "Coach Home"
    assert snap.away_coach.coach.name == "Coach Away"
    assert snap.home_squad.starting_xi_confidence == 0.48
    assert snap.home_squad.missing_players_count == 0
    assert len(snap.home_squad.expected_starting_xi) == 2
    assert snap.home_squad.expected_starting_xi[0].name == "Player H1"


def test_brave_coach_hints_not_lost_when_squad_unknown() -> None:
    facts = _facts()
    facts = facts.model_copy(
        update={
            "squad_raw": facts.squad_raw.model_copy(
                update={"coach_name_home": "Unknown", "coach_name_away": "Unknown"},
            )
            if facts.squad_raw
            else None,
        },
    )
    news = MatchNewsContext(
        source_count=2,
        confidence=0.6,
        coach=CoachContextBlock(
            home_coach_name="Brave Coach Home",
            away_coach_name="Brave Coach Away",
            coach_news_confidence=0.55,
            home_coach_tenure_days=120,
        ),
        general_news=GeneralNewsBlock(predicted_lineup_signals=["possible rotation"]),
    )
    odds = _odds()
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds, news_context=news)
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)

    assert snap.home_coach.coach.name == "Brave Coach Home"
    assert snap.away_coach.coach.name == "Brave Coach Away"
    assert snap.home_coach.days_in_charge == 120
    assert snap.confidence.coaches_confidence >= 0.5


def test_non_league_path_still_builds_snapshot() -> None:
    facts = _facts()
    assert facts is not None
    meta = facts.meta.model_copy(update={"tournament_type": TournamentType.DOMESTIC_CUP})
    cup_facts = facts.model_copy(update={"meta": meta})
    derived = derive_season_motivation(cup_facts)
    merged = merge_match_context_v2(
        facts=cup_facts,
        openclaw_context=None,
        odds_context=None,
        news_context=None,
    )
    merged = merged.model_copy(update={"derived_season_motivation": derived})
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)

    assert snap.match_meta.tournament_type == TournamentType.DOMESTIC_CUP
    assert snap.confidence is not None
    assert isinstance(report.builder_warnings, list)


def test_sparse_facts_remain_fail_soft() -> None:
    svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES_DIR))
    facts = svc.get_facts_for_match("flashscore_botola_sample_match")
    assert facts is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    snap, _ = MergedSnapshotBuilderV2().build_with_report(merged)

    assert snap.home_team_context.form.last_5_form_score == 0.5
    assert snap.home_schedule.days_since_last_match == 8
    assert snap.confidence.teams_confidence >= 0.0
