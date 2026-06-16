"""Squads + odds enrichment tests (snapshot path, no routing changes)."""

from __future__ import annotations

from pathlib import Path

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.news_context.models import GeneralNewsBlock, MatchNewsContext
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.normalizers.squad_snapshot_helpers import squad_context_from_raw
from football_agent.domain.models_v2 import TeamRefV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.services.scoring_service_v2 import ScoringServiceV2

FIXTURES = Path(__file__).parent / "data"


def _facts(stem: str):
    svc = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES))
    return svc.get_facts_for_match(stem)


def _odds(stem: str = "odds_sample"):
    return OddsIngestionService(FixtureFileOddsAdapter(FIXTURES)).get_odds_for_fixture(stem)


def _build(stem: str, *, odds_stem: str | None = "odds_sample", news=None):
    facts = _facts(stem)
    odds = _odds(odds_stem) if odds_stem else None
    assert facts is not None
    merged = merge_match_context_v2(
        facts=facts,
        openclaw_context=None,
        odds_context=odds,
        news_context=news,
    )
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    return snap, merged, report


def test_rich_squad_snapshot_fields_populated() -> None:
    snap, _, _ = _build("flashscore_squad_rich_match", odds_stem=None)

    home = snap.home_squad
    assert home.starting_xi_confidence > 0.5
    assert home.missing_players_count >= 1
    assert home.missing_key_players_count >= 1
    assert len(home.expected_starting_xi) == 3
    assert len(home.suspended_players) == 0
    assert len(home.doubtful_players) >= 1
    assert home.availability_score < 0.85
    assert home.key_absence_impact_score > 0.0

    away = snap.away_squad
    assert away.starting_xi_confidence >= 0.35
    assert away.missing_key_players_count >= 1
    assert len(away.suspended_players) >= 1
    assert snap.home_team_context.availability_score == home.availability_score


def test_partial_brave_injury_hints_enrich_squad_without_fake_xi() -> None:
    facts = _facts("flashscore_sample_league_match")
    assert facts is not None
    team = TeamRefV2(team_id=1, name="AC Milan")
    news = MatchNewsContext(
        source_count=2,
        confidence=0.55,
        home_team="AC Milan",
        away_team="Juventus",
        general_news=GeneralNewsBlock(
            injuries_signals=["AC Milan striker ruled out"],
            predicted_lineup_signals=["Juventus midfielder doubtful for kickoff"],
        ),
    )
    squad = squad_context_from_raw(
        facts.squad_raw,
        team,
        side="home",
        news_context=news,
        home_team="AC Milan",
        away_team="Juventus",
    )
    assert squad.starting_xi_confidence > 0.2
    assert squad.missing_players_count >= 1
    assert squad.key_absence_impact_score > 0.0
    assert squad.starting_xi_confidence < 0.7
    assert any(
        p.player.name.startswith("news_hint:") or p.player.name
        for p in squad.missing_players
    )


def test_brave_hints_do_not_break_factual_squad_block() -> None:
    snap, _, _ = _build(
        "flashscore_squad_rich_match",
        odds_stem=None,
        news=MatchNewsContext(
            source_count=1,
            confidence=0.5,
            general_news=GeneralNewsBlock(injuries_signals=["AC Milan winger injury concern"]),
        ),
    )
    assert snap.home_squad.missing_players_count >= 1
    assert any("Milan Striker" in p.player.name for p in snap.home_squad.missing_players)


def test_odds_reach_snapshot_and_scorer_with_aligned_fixture() -> None:
    snap, merged, report = _build("flashscore_sample_league_match")
    assert merged.provenance.odds_link_strategy == "by_match_id"
    assert snap.odds.home_win is not None
    assert snap.odds.home_not_lose is not None
    assert snap.odds.btts_yes is not None
    assert snap.odds.odds_confidence > 0.3

    scored = ScoringServiceV2().score_snapshot_with_report(snap, report)
    assert scored.prediction.best_market is not None
    assert scored.prediction.best_market.book_odds is not None
    assert any(m.book_odds is not None for m in scored.prediction.market_predictions)


def test_partial_odds_preserved_in_snapshot() -> None:
    odds = _odds("odds_partial_sample")
    facts = _facts("flashscore_sample_league_match")
    assert facts is not None and odds is not None
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    assert snap.odds.home_win is not None
    assert snap.odds.away_win is None
    assert any("odds_partial_snapshot_markets" in w for w in report.builder_warnings)
    assert snap.odds.odds_confidence > 0.15


def test_odds_teams_only_link_when_date_missing() -> None:
    facts = _facts("flashscore_sample_league_match")
    odds = _odds()
    assert facts is not None and odds is not None
    odds = odds.model_copy(
        update={
            "meta": odds.meta.model_copy(
                update={"match_id": None, "fixture_id": "", "kickoff_utc": None},
            ),
        },
    )
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=odds)
    assert merged.provenance.odds_link_strategy == "by_teams_and_date"
    assert "odds_link_teams_only_missing_date" in merged.provenance.warnings


def test_parked_cup_path_unchanged_with_richer_inputs() -> None:
    from football_agent.domain.enums_v2 import TournamentType

    facts = _facts("flashscore_sample_league_match")
    assert facts is not None
    cup = facts.model_copy(update={"meta": facts.meta.model_copy(update={"tournament_type": TournamentType.DOMESTIC_CUP})})
    odds = _odds()
    merged = merge_match_context_v2(facts=cup, openclaw_context=None, odds_context=odds)
    snap, report = MergedSnapshotBuilderV2().build_with_report(merged)
    scored = ScoringServiceV2().score_snapshot_with_report(snap, report)
    assert scored.prediction.analysis_mode == "analysis_only"
    assert scored.prediction.best_market is None
