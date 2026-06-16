"""Stage 3: competition-agnostic policy (registry + env allow/deny)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from football_agent.competition_policy import (
    WARN_NOT_IN_EXPRESS_ALLOWLIST,
    WARN_UNKNOWN_COMPETITION,
    express_allowed_codes_effective,
    filter_for_express,
    is_analysis_allowed,
    is_express_allowed,
)
from football_agent.domain.enums_v2 import ExpressSafetyClass
from football_agent.domain.models_v2 import (
    ExpressScreeningV2,
    LeagueFactorScoresV2,
    MarketPredictionV2,
    MatchMetaV2,
    MatchPredictionResultV2,
    TeamRefV2,
    TeamScoringResultV2,
)
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.league_registry import (
    LeagueConfig,
    list_football_data_discovery_codes,
    list_registry_codes,
    register_league,
)


def _prediction(competition_code: str, match_id: int = 1) -> MatchPredictionResultV2:
    home = TeamRefV2(team_id=1, name="Home", short_name="Home")
    away = TeamRefV2(team_id=2, name="Away", short_name="Away")
    from datetime import datetime, timezone

    meta = MatchMetaV2(
        match_id=match_id,
        season=2025,
        competition_name="Test",
        competition_code=competition_code,
        match_date_utc=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        home_team=home,
        away_team=away,
    )
    market = MarketPredictionV2(
        market_key="HOME_NOT_LOSE",
        probability=0.8,
        book_odds=1.35,
        label="1X",
    )
    return MatchPredictionResultV2(
        match_meta=meta,
        home_scoring=TeamScoringResultV2(team=home, factor_scores=LeagueFactorScoresV2()),
        away_scoring=TeamScoringResultV2(team=away, factor_scores=LeagueFactorScoresV2()),
        best_market=market,
        express_safety=ExpressScreeningV2(
            safety_class=ExpressSafetyClass.EXPRESS_SAFE,
            allow_for_express=True,
        ),
        overall_confidence_score=0.7,
    )


def test_botola_registered_and_express_allowed() -> None:
    decision = is_express_allowed("FS_BOTOLA_PRO")
    assert decision.allowed is True
    assert decision.reason == "registry"
    analysis = is_analysis_allowed("FS_BOTOLA_PRO")
    assert analysis.allowed is True


def test_unknown_competition_analysis_soft_allow_express_soft_deny() -> None:
    analysis = is_analysis_allowed("MYSTERY_LEAGUE_X")
    assert analysis.allowed is True
    assert analysis.warning == WARN_UNKNOWN_COMPETITION

    express = is_express_allowed("MYSTERY_LEAGUE_X")
    assert express.allowed is False
    assert express.warning == WARN_UNKNOWN_COMPETITION


def test_express_env_allowlist_includes_non_top5() -> None:
    with patch.dict(os.environ, {"LEAGUE_EXPRESS_ALLOWED_CODES": "FS_BOTOLA_PRO,PL"}):
        assert is_express_allowed("FS_BOTOLA_PRO").allowed is True
        assert is_express_allowed("SA").allowed is False
        miss = is_express_allowed("SA")
        assert miss.warning == WARN_NOT_IN_EXPRESS_ALLOWLIST


def test_express_builder_accepts_botola_candidate() -> None:
    results = [
        _prediction("FS_BOTOLA_PRO", match_id=10),
        _prediction("FS_BOTOLA_PRO", match_id=11),
    ]
    results[1].match_meta.match_id = 11
    bet = ExpressBuilderV2().build_express(results, target_odds=1.8, max_events=2)
    assert bet is not None
    assert len(bet.events) == 2


def test_express_builder_skips_unknown_competition_fail_soft() -> None:
    good = _prediction("PL", match_id=1)
    bad = _prediction("UNKNOWN_CUP_X", match_id=2)
    bet = ExpressBuilderV2().build_express([good, bad], target_odds=1.5, max_events=2)
    assert bet is not None
    assert len(bet.events) == 1
    assert bet.events[0].match_meta.competition_code == "PL"


def test_filter_for_express_preserves_order() -> None:
    items = [_prediction("PL", 1), _prediction("UNKNOWN", 2), _prediction("FS_BOTOLA_PRO", 3)]
    kept = filter_for_express(items, log_skips=False)
    assert [r.match_meta.match_id for r in kept] == [1, 3]


def test_football_data_discovery_still_top5_only() -> None:
    discovery = list_football_data_discovery_codes()
    assert discovery == ["BL1", "FL1", "PD", "PL", "SA"]
    assert "FS_BOTOLA_PRO" not in discovery
    assert "FS_BOTOLA_PRO" in list_registry_codes()


def test_registry_runtime_extension_for_express() -> None:
    register_league(
        LeagueConfig(
            competition_code="TEST_STAGE3",
            display_name="Stage3 Test",
            football_data_discoverable=False,
            express_allowed=True,
        )
    )
    try:
        assert is_express_allowed("TEST_STAGE3").allowed is True
        assert "TEST_STAGE3" in express_allowed_codes_effective()
    finally:
        from football_agent.league_registry import _REGISTRY

        _REGISTRY.pop("TEST_STAGE3", None)
