"""
Central policy for competition-family guardrails (baseline / express / eval pool).

Default product stance:
- MEN_SENIOR_LEAGUE: league baseline, evalpool default, express allowed (subject to registry).
- WOMEN_SENIOR_LEAGUE: analysis on explicit request; express denied until dedicated blueprint.
- YOUTH / RESERVES / OTHER_SPECIAL: read-only / low-priority; excluded from baseline & express.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Flag, auto
from typing import FrozenSet, Optional, Set

from football_agent.domain.competition_family import (
    CompetitionFamily,
    CompetitionFamilyMeta,
    classify_competition_family,
    pool_entry_accepts_family,
)

ENV_EVAL_POOL_FAMILIES = "EVAL_POOL_COMPETITION_FAMILIES"
ENV_BASELINE_FAMILIES = "LEAGUE_BASELINE_COMPETITION_FAMILIES"


class EvalPoolFamilyMode(Flag):
    MEN_SENIOR_ONLY = auto()
    INCLUDE_WOMEN = auto()
    INCLUDE_YOUTH = auto()
    INCLUDE_RESERVES = auto()
    INCLUDE_SPECIAL = auto()

    @classmethod
    def default_baseline(cls) -> "EvalPoolFamilyMode":
        return cls.MEN_SENIOR_ONLY

    @classmethod
    def wave_all_registered(cls) -> "EvalPoolFamilyMode":
        return cls.MEN_SENIOR_ONLY | cls.INCLUDE_WOMEN


DEFAULT_BASELINE_FAMILIES: FrozenSet[CompetitionFamily] = frozenset({CompetitionFamily.MEN_SENIOR_LEAGUE})
DEFAULT_EXPRESS_FAMILIES: FrozenSet[CompetitionFamily] = frozenset({CompetitionFamily.MEN_SENIOR_LEAGUE})

WARN_FAMILY_NOT_IN_BASELINE = "competition_family_not_in_baseline"
WARN_FAMILY_EXPRESS_DENIED = "competition_family_express_denied"
WARN_FAMILY_POOL_MISMATCH = "competition_family_pool_mismatch"


@dataclass(frozen=True)
class FamilyPolicyDecision:
    allowed: bool
    family: CompetitionFamily
    reason: str
    warning: Optional[str] = None


def _families_from_env(env_var: str) -> Optional[FrozenSet[CompetitionFamily]]:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return None
    out: Set[CompetitionFamily] = set()
    for part in raw.split(","):
        token = part.strip().upper()
        if not token:
            continue
        try:
            out.add(CompetitionFamily(token))
        except ValueError:
            continue
    return frozenset(out) if out else None


def eval_pool_family_mode_from_env() -> EvalPoolFamilyMode:
    raw = (os.getenv(ENV_EVAL_POOL_FAMILIES) or "").strip().lower()
    if not raw or raw in ("men_senior", "men_senior_only", "default"):
        return EvalPoolFamilyMode.default_baseline()
    mode = EvalPoolFamilyMode.MEN_SENIOR_ONLY
    if "women" in raw:
        mode |= EvalPoolFamilyMode.INCLUDE_WOMEN
    if "youth" in raw:
        mode |= EvalPoolFamilyMode.INCLUDE_YOUTH
    if "reserve" in raw:
        mode |= EvalPoolFamilyMode.INCLUDE_RESERVES
    if "special" in raw:
        mode |= EvalPoolFamilyMode.INCLUDE_SPECIAL
    if raw in ("all", "wave_all"):
        return EvalPoolFamilyMode.wave_all_registered()
    return mode


def families_for_eval_mode(mode: EvalPoolFamilyMode) -> FrozenSet[CompetitionFamily]:
    env = _families_from_env(ENV_EVAL_POOL_FAMILIES)
    if env is not None:
        return env
    allowed: Set[CompetitionFamily] = set()
    if mode & EvalPoolFamilyMode.MEN_SENIOR_ONLY:
        allowed.add(CompetitionFamily.MEN_SENIOR_LEAGUE)
    if mode & EvalPoolFamilyMode.INCLUDE_WOMEN:
        allowed.add(CompetitionFamily.WOMEN_SENIOR_LEAGUE)
    if mode & EvalPoolFamilyMode.INCLUDE_YOUTH:
        allowed.add(CompetitionFamily.YOUTH_UXX)
    if mode & EvalPoolFamilyMode.INCLUDE_RESERVES:
        allowed.add(CompetitionFamily.RESERVES)
    if mode & EvalPoolFamilyMode.INCLUDE_SPECIAL:
        allowed.add(CompetitionFamily.OTHER_SPECIAL)
    return frozenset(allowed) if allowed else DEFAULT_BASELINE_FAMILIES


def baseline_families_effective() -> FrozenSet[CompetitionFamily]:
    env = _families_from_env(ENV_BASELINE_FAMILIES)
    return env if env is not None else DEFAULT_BASELINE_FAMILIES


def is_baseline_family(family: CompetitionFamily) -> bool:
    return family in baseline_families_effective()


def is_express_family_allowed(family: CompetitionFamily) -> FamilyPolicyDecision:
    if family in DEFAULT_EXPRESS_FAMILIES:
        return FamilyPolicyDecision(True, family, "men_senior_default")
    return FamilyPolicyDecision(
        False,
        family,
        "family_express_denied",
        warning=WARN_FAMILY_EXPRESS_DENIED,
    )


def is_analysis_family_allowed(
    family: CompetitionFamily,
    *,
    explicit_request: bool = False,
) -> FamilyPolicyDecision:
    if family == CompetitionFamily.MEN_SENIOR_LEAGUE:
        return FamilyPolicyDecision(True, family, "men_senior_default")
    if family == CompetitionFamily.WOMEN_SENIOR_LEAGUE:
        if explicit_request:
            return FamilyPolicyDecision(True, family, "women_explicit_request")
        return FamilyPolicyDecision(
            False,
            family,
            "women_requires_explicit_request",
            warning=WARN_FAMILY_NOT_IN_BASELINE,
        )
    if explicit_request:
        return FamilyPolicyDecision(True, family, "special_explicit_request")
    return FamilyPolicyDecision(
        False,
        family,
        "non_baseline_family",
        warning=WARN_FAMILY_NOT_IN_BASELINE,
    )


def resolve_family_meta(
    *,
    competition_code: Optional[str] = None,
    competition_name: Optional[str] = None,
    country: Optional[str] = None,
) -> CompetitionFamilyMeta:
    return classify_competition_family(
        competition_code=competition_code,
        competition_name=competition_name,
        country=country,
    )


def eval_pool_family_allowed(
    *,
    competition_code: Optional[str] = None,
    competition_name: Optional[str] = None,
    competition_country: Optional[str] = None,
    pool_registry_code: Optional[str] = None,
    mode: Optional[EvalPoolFamilyMode] = None,
) -> FamilyPolicyDecision:
    meta = resolve_family_meta(
        competition_code=competition_code,
        competition_name=competition_name,
        country=competition_country,
    )
    allowed_families = families_for_eval_mode(mode or eval_pool_family_mode_from_env())
    if meta.family not in allowed_families:
        return FamilyPolicyDecision(
            False,
            meta.family,
            "family_not_in_eval_mode",
            warning=WARN_FAMILY_NOT_IN_BASELINE,
        )
    if pool_registry_code and not pool_entry_accepts_family(pool_registry_code, meta.family):
        return FamilyPolicyDecision(
            False,
            meta.family,
            "family_pool_entry_mismatch",
            warning=WARN_FAMILY_POOL_MISMATCH,
        )
    return FamilyPolicyDecision(True, meta.family, "eval_pool_allowed")
