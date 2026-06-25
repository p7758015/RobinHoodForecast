"""Competition family classification and policy tests."""

from __future__ import annotations

from football_agent.competition_family_policy import (
    WARN_FAMILY_EXPRESS_DENIED,
    eval_pool_family_allowed,
    is_express_family_allowed,
)
from football_agent.competition_policy import is_express_allowed
from football_agent.domain.competition_family import (
    CompetitionFamily,
    classify_competition_family,
    resolve_competition_identity,
)
from football_agent.eval_pool.scope import resolve_pool_entry
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.tests.test_competition_policy import _prediction


def test_fs_meistriliiga_women_slug() -> None:
    meta = classify_competition_family(
        competition_code="FS_MEISTRILIIGA_WOMEN",
        competition_name="Meistriliiga Women",
        country="Estonia",
    )
    assert meta.family == CompetitionFamily.WOMEN_SENIOR_LEAGUE
    assert meta.is_women is True


def test_registry_premium_liiga_women() -> None:
    meta = classify_competition_family(
        competition_code="FS_ESTONIA_PREMIUM_LIIGA",
        competition_name="Premium Liiga",
    )
    assert meta.family == CompetitionFamily.WOMEN_SENIOR_LEAGUE
    assert meta.source == "registry"


def test_men_serie_b_default() -> None:
    meta = classify_competition_family(
        competition_code="FS_BRAZIL_SERIE_B",
        competition_name="Serie B",
        country="Brazil",
    )
    assert meta.family == CompetitionFamily.MEN_SENIOR_LEAGUE


def test_youth_u19_from_code() -> None:
    meta = classify_competition_family(
        competition_code="FS_PREMIER_LEAGUE_U19",
        competition_name="Premier League U19",
    )
    assert meta.family == CompetitionFamily.YOUTH_UXX
    assert meta.is_youth is True
    assert meta.subtype == "U19"


def test_resolve_pool_entry_women_vs_men_estonia() -> None:
    men = resolve_pool_entry("Meistriliiga", "Estonia")
    women = resolve_pool_entry("Meistriliiga Women", "Estonia")
    assert men is not None and men.key == "estonia_meistriliiga"
    assert women is not None and women.key == "estonia_premium_liiga"


def test_resolve_competition_identity_maps_women_to_registry() -> None:
    code, meta = resolve_competition_identity("Meistriliiga Women", "Estonia")
    assert code == "FS_ESTONIA_PREMIUM_LIIGA"
    assert meta.family == CompetitionFamily.WOMEN_SENIOR_LEAGUE


def test_women_express_denied_by_family() -> None:
    decision = is_express_allowed("FS_ESTONIA_PREMIUM_LIIGA", competition_name="Premium Liiga")
    assert decision.allowed is False
    assert decision.warning == WARN_FAMILY_EXPRESS_DENIED


def test_express_builder_skips_women_match() -> None:
    good = _prediction("FS_BRAZIL_SERIE_B", match_id=1)
    women = _prediction("FS_ESTONIA_PREMIUM_LIIGA", match_id=2)
    women.match_meta = women.match_meta.model_copy(
        update={"competition_name": "Premium Liiga", "competition_family": "WOMEN_SENIOR_LEAGUE", "is_women": True},
    )
    bet = ExpressBuilderV2().build_express([good, women], target_odds=1.5, max_events=2)
    assert bet is not None
    assert len(bet.events) == 1
    assert bet.events[0].match_meta.competition_code == "FS_BRAZIL_SERIE_B"


def test_eval_pool_family_mismatch_skipped() -> None:
    from football_agent.competition_family_policy import EvalPoolFamilyMode

    decision = eval_pool_family_allowed(
        competition_name="Meistriliiga Women",
        competition_country="Estonia",
        pool_registry_code="FS_ESTONIA_MEISTRILIIGA",
        mode=EvalPoolFamilyMode.MEN_SENIOR_ONLY,
    )
    assert decision.allowed is False

    allowed_women = eval_pool_family_allowed(
        competition_name="Meistriliiga Women",
        competition_country="Estonia",
        pool_registry_code="FS_ESTONIA_PREMIUM_LIIGA",
        mode=EvalPoolFamilyMode.MEN_SENIOR_ONLY | EvalPoolFamilyMode.INCLUDE_WOMEN,
    )
    assert allowed_women.allowed is True


def test_is_express_family_allowed_men_only() -> None:
    assert is_express_family_allowed(CompetitionFamily.MEN_SENIOR_LEAGUE).allowed is True
    assert is_express_family_allowed(CompetitionFamily.WOMEN_SENIOR_LEAGUE).allowed is False
