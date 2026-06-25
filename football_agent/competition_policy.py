"""
Competition allow/deny policy for analysis and express (Stage 3).

Replaces the legacy product assumption "express only for top-5 European leagues".
League eligibility is driven by registry metadata + optional env allow/deny lists.
Unknown competitions fail soft: analysis may proceed with defaults; express skips with WARNING.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Set

from football_agent.competition_family_policy import (
    WARN_FAMILY_EXPRESS_DENIED,
    is_express_family_allowed,
    resolve_family_meta,
)
from football_agent.league_registry import (
    LeagueConfig,
    _normalize_code,
    get_league_config,
)

logger = logging.getLogger(__name__)

ENV_ANALYSIS_ALLOWED = "LEAGUE_ANALYSIS_ALLOWED_CODES"
ENV_EXPRESS_ALLOWED = "LEAGUE_EXPRESS_ALLOWED_CODES"
ENV_DENY_CODES = "LEAGUE_DENY_CODES"

WARN_UNKNOWN_COMPETITION = "unknown_competition_code"
WARN_NOT_IN_ANALYSIS_ALLOWLIST = "competition_not_in_analysis_allowlist"
WARN_NOT_IN_EXPRESS_ALLOWLIST = "competition_not_in_express_allowlist"
WARN_REGISTRY_ANALYSIS_DISABLED = "competition_analysis_disabled_in_registry"
WARN_REGISTRY_EXPRESS_DISABLED = "competition_express_disabled_in_registry"
WARN_ENV_DENY_LIST = "competition_denied_by_env"


@dataclass(frozen=True)
class CompetitionPolicyDecision:
    allowed: bool
    competition_code: str
    reason: str
    warning: Optional[str] = None


def _parse_env_code_set(env_var: str) -> Optional[Set[str]]:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return None
    return {_normalize_code(part) for part in raw.split(",") if part.strip()}


def _deny_set() -> Set[str]:
    return _parse_env_code_set(ENV_DENY_CODES) or set()


def _analysis_allow_set() -> Optional[Set[str]]:
    return _parse_env_code_set(ENV_ANALYSIS_ALLOWED)


def _express_allow_set() -> Optional[Set[str]]:
    return _parse_env_code_set(ENV_EXPRESS_ALLOWED)


def is_registered_competition(competition_code: str) -> bool:
    cfg = get_league_config(competition_code)
    return cfg is not None


def is_analysis_allowed(competition_code: str) -> CompetitionPolicyDecision:
    """
    Whether a competition may be analyzed (bulk or single-match routing).

    Unknown codes: allowed with warning (defaults via resolve_league_params).
    """
    code = _normalize_code(competition_code) or "UNKNOWN"
    deny = _deny_set()
    if code in deny:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="env_deny",
            warning=WARN_ENV_DENY_LIST,
        )

    env_allow = _analysis_allow_set()
    if env_allow is not None:
        if code in env_allow:
            return CompetitionPolicyDecision(
                allowed=True,
                competition_code=code,
                reason="env_allowlist",
            )
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="env_allowlist_miss",
            warning=WARN_NOT_IN_ANALYSIS_ALLOWLIST,
        )

    cfg = get_league_config(code)
    if cfg is None:
        return CompetitionPolicyDecision(
            allowed=True,
            competition_code=code,
            reason="unknown_soft_allow",
            warning=WARN_UNKNOWN_COMPETITION,
        )
    if not cfg.analysis_allowed:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="registry_disabled",
            warning=WARN_REGISTRY_ANALYSIS_DISABLED,
        )
    return CompetitionPolicyDecision(
        allowed=True,
        competition_code=code,
        reason="registry",
    )


def is_express_allowed(
    competition_code: str,
    *,
    competition_name: Optional[str] = None,
    competition_country: Optional[str] = None,
) -> CompetitionPolicyDecision:
    """
    Whether a scored match from this competition may enter express candidate pool.

    Unknown / unregistered codes: denied (soft skip + warning).
    Non men-senior families: denied by default.
    """
    code = _normalize_code(competition_code) or "UNKNOWN"
    family_meta = resolve_family_meta(
        competition_code=code,
        competition_name=competition_name,
        country=competition_country,
    )
    family_decision = is_express_family_allowed(family_meta.family)
    if not family_decision.allowed:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason=family_decision.reason,
            warning=family_decision.warning or WARN_FAMILY_EXPRESS_DENIED,
        )

    deny = _deny_set()
    if code in deny:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="env_deny",
            warning=WARN_ENV_DENY_LIST,
        )

    env_allow = _express_allow_set()
    if env_allow is not None:
        if code in env_allow:
            return CompetitionPolicyDecision(
                allowed=True,
                competition_code=code,
                reason="env_allowlist",
            )
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="env_allowlist_miss",
            warning=WARN_NOT_IN_EXPRESS_ALLOWLIST,
        )

    cfg = get_league_config(code)
    if cfg is None:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="unknown_soft_deny",
            warning=WARN_UNKNOWN_COMPETITION,
        )
    if not cfg.express_allowed:
        return CompetitionPolicyDecision(
            allowed=False,
            competition_code=code,
            reason="registry_disabled",
            warning=WARN_REGISTRY_EXPRESS_DISABLED,
        )
    return CompetitionPolicyDecision(
        allowed=True,
        competition_code=code,
        reason="registry",
    )


def filter_for_express(
    results: List,
    *,
    log_skips: bool = True,
) -> List:
    """
    Filter MatchPredictionResultV2 (or compatible) list for express assembly.

    Preserves order; skips disallowed competitions with WARNING (fail-soft).
    """
    kept: List = []
    for result in results:
        meta = getattr(result, "match_meta", None)
        code = getattr(meta, "competition_code", None) if meta is not None else None
        name = getattr(meta, "competition_name", None) if meta is not None else None
        country = getattr(meta, "country", None) if meta is not None else None
        decision = is_express_allowed(str(code or ""), competition_name=name, competition_country=country)
        if decision.allowed:
            kept.append(result)
            continue
        if log_skips:
            match_id = getattr(meta, "match_id", "?") if meta is not None else "?"
            logger.warning(
                "Express policy skip match_id=%s competition=%s reason=%s warning=%s",
                match_id,
                decision.competition_code,
                decision.reason,
                decision.warning,
            )
    return kept


def express_allowed_codes_effective() -> List[str]:
    """Resolved express pool for diagnostics (registry + env)."""
    env_allow = _express_allow_set()
    deny = _deny_set()
    if env_allow is not None:
        codes = sorted(env_allow - deny)
    else:
        codes = sorted(
            cfg.competition_code
            for cfg in _all_registry_configs()
            if cfg.express_allowed and cfg.competition_code not in deny
        )
    return codes


def _all_registry_configs() -> List[LeagueConfig]:
    from football_agent.league_registry import list_registry_codes

    out: List[LeagueConfig] = []
    for code in list_registry_codes():
        cfg = get_league_config(code)
        if cfg is not None:
            out.append(cfg)
    return out
