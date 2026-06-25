"""Tests for coach name normalization and factor mapping."""

from __future__ import annotations

from football_agent.domain.enums_v2 import MotivationContext
from football_agent.domain.models_v2 import TeamMotivationBlockV2
from football_agent.news_context.coach_normalize import normalize_coach_name
from football_agent.news_context.factor_mapping import (
    apply_brave_motivation_bias,
    extract_player_hint_from_signal,
)
from football_agent.news_context.models import GeneralNewsBlock, MatchNewsContext


def test_normalize_coach_name_strips_trailing_pt_verb() -> None:
    assert normalize_coach_name("Umberto Louzer terá") == "Umberto Louzer"
    assert normalize_coach_name("técnico Umberto Louzer terá") == "Umberto Louzer"
    assert normalize_coach_name("Coach John Smith said") == "John Smith"


def test_extract_goalkeeper_from_pt_signal() -> None:
    text = (
        "o técnico Umberto Louzer terá um desfalque importantíssimo: "
        "o goleiro Gustavo, suspenso do duelo"
    )
    name, role = extract_player_hint_from_signal(text)
    assert name == "Gustavo"
    assert role == "goalkeeper"


def test_brave_motivation_bias_lanterna_home() -> None:
    from football_agent.news_context.models import GeneralNewsBlock

    block = TeamMotivationBlockV2(motivation_score=0.4, motivation_context=MotivationContext.MIDTABLE_NEUTRAL)
    news = MatchNewsContext(
        source_count=3,
        confidence=0.6,
        general_news=GeneralNewsBlock(
            home_motivation_signals=["lanterna América-MG tenta escapar do Z4"],
        ),
        sources=[],
    )
    out = apply_brave_motivation_bias(
        block,
        news,
        side="home",
        home_team="America MG",
        away_team="Criciuma",
    )
    assert out.motivation_score > 0.4
    assert out.motivation_context == MotivationContext.RELEGATION_BATTLE
