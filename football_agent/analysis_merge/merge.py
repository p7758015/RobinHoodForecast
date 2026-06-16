"""Merge logic for Flashscore facts (+ derived) and optional context blocks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Tuple

from football_agent.analysis_merge.models import (
    MatchLinkStrategy,
    MergeProvenance,
    MergedHeadline,
    MergedMatchAnalysisContext,
)
from football_agent.flashscore.derived_season import derive_season_motivation
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.models import MatchOddsContext
from football_agent.odds.service import MARKET_FIELDS
from football_agent.news_context.models import MatchNewsContext
from football_agent.openclaw_context.models import OpenClawMatchContext


def merge_flashscore_and_openclaw_context(
    facts: FlashscoreMatchFacts,
    context: Optional[OpenClawMatchContext],
) -> MergedMatchAnalysisContext:
    """
    Backwards-compatible wrapper for the older merge signature.

    Prefer `merge_match_context_v2(...)` for the v2 contract that also accepts odds.
    """
    return merge_match_context_v2(facts=facts, openclaw_context=context, odds_context=None)


def merge_match_context_v2(
    *,
    facts: FlashscoreMatchFacts,
    openclaw_context: Optional[OpenClawMatchContext],
    odds_context: Optional[MatchOddsContext],
    news_context: Optional[MatchNewsContext] = None,
) -> MergedMatchAnalysisContext:
    """Merge Flashscore facts (+ derived) with optional OpenClaw context + optional odds."""
    derived = derive_season_motivation(facts)

    openclaw_strategy, warnings = _link_strategy_openclaw(facts, openclaw_context)
    odds_strategy, odds_warnings = _link_strategy_odds(facts, odds_context)
    warnings.extend(odds_warnings)

    blocks_present = ["flashscore_facts", "derived_season_motivation"]
    missing_blocks = []
    if openclaw_context is not None:
        blocks_present.append("openclaw_context")
    else:
        missing_blocks.append("openclaw_context")
        warnings.append("openclaw_context_not_provided")

    if odds_context is not None:
        blocks_present.append("odds_context")
    else:
        missing_blocks.append("odds_context")
        warnings.append("odds_context_not_provided")

    if news_context is not None:
        blocks_present.append("news_context")
    else:
        missing_blocks.append("news_context")

    odds_values = _headline_odds_values(odds_context)
    odds_missing_count = _odds_missing_count(odds_context)

    headline = MergedHeadline(
        home_team=facts.meta.home_team_name,
        away_team=facts.meta.away_team_name,
        competition_name=facts.meta.competition_name,
        kickoff_utc=facts.meta.kickoff_utc,
        season_phase=derived.season_phase,
        gap_to_title_points=derived.gap_to_title_points,
        gap_to_europe_points=derived.gap_to_europe_points,
        gap_to_relegation_safety_points=derived.gap_to_relegation_safety_points,
        openclaw_context_present=openclaw_context is not None,
        odds_present=odds_context is not None,
        odds_missing_count=odds_missing_count,
        **odds_values,
    )

    prov = MergeProvenance(
        match_link_strategy=openclaw_strategy,
        odds_link_strategy=odds_strategy,
        blocks_present=blocks_present,
        missing_blocks=missing_blocks,
        warnings=warnings,
    )

    return MergedMatchAnalysisContext(
        headline=headline,
        flashscore_facts=facts,
        derived_season_motivation=derived,
        openclaw_context=openclaw_context,
        odds_context=odds_context,
        news_context=news_context,
        provenance=prov,
    )


def _link_strategy_openclaw(
    facts: FlashscoreMatchFacts,
    context: Optional[OpenClawMatchContext],
) -> Tuple[MatchLinkStrategy, list[str]]:
    warnings: list[str] = []
    if context is None:
        return "unlinked", warnings

    # 1) by_match_id: exact match (if OpenClaw context later uses same id)
    if context.meta.match_id and facts.meta.match_id and str(context.meta.match_id) == str(facts.meta.match_id):
        return "by_match_id", warnings

    # 2) by_query_string: exact match to normalized query string
    flashscore_qs = _build_flashscore_query_string(facts)
    if context.meta.query_string and flashscore_qs and _norm(context.meta.query_string) == _norm(flashscore_qs):
        return "by_query_string", warnings

    # 3) by_teams_and_date: compare normalized teams + date when available
    if _teams_and_date_match(facts, context):
        return "by_teams_and_date", warnings

    warnings.append("context_provided_but_not_linked")
    return "provided_without_link", warnings


def _link_strategy_odds(
    facts: FlashscoreMatchFacts,
    odds: Optional[MatchOddsContext],
) -> Tuple[MatchLinkStrategy, list[str]]:
    warnings: list[str] = []
    if odds is None:
        return "unlinked", warnings

    fs_mid = str(facts.meta.match_id or "").strip()
    od_mid = str(odds.meta.match_id or "").strip()
    od_fid = str(odds.meta.fixture_id or "").strip()

    # 1) by_match_id (explicit or fixture_id alias)
    if fs_mid and od_mid and fs_mid == od_mid:
        return "by_match_id", warnings
    if fs_mid and od_fid and fs_mid == od_fid:
        return "by_match_id", warnings

    # 2) by_query_string
    flashscore_qs = _build_flashscore_query_string(facts)
    if odds.meta.query_string and flashscore_qs and _norm(odds.meta.query_string) == _norm(flashscore_qs):
        return "by_query_string", warnings

    # 3) by_teams_and_date (strict when both dates present)
    if _teams_and_date_match_odds(facts, odds):
        return "by_teams_and_date", warnings

    # 4) by_teams_only — canonical team keys match; dates missing or skipped (safe, no fuzzy)
    if _teams_canonical_match_odds(facts, odds):
        fs_date = facts.meta.kickoff_utc.date() if facts.meta.kickoff_utc else None
        od_date = odds.meta.kickoff_utc.date() if odds.meta.kickoff_utc else None
        if fs_date is None or od_date is None:
            warnings.append("odds_link_teams_only_missing_date")
            return "by_teams_and_date", warnings
        if fs_date != od_date:
            warnings.append("odds_link_teams_only_date_mismatch")
            return "by_teams_and_date", warnings

    warnings.append("odds_provided_but_not_linked")
    return "provided_without_link", warnings


def _build_flashscore_query_string(facts: FlashscoreMatchFacts) -> Optional[str]:
    if not facts.meta.home_team_name or not facts.meta.away_team_name:
        return None
    d: Optional[date] = facts.meta.kickoff_utc.date() if facts.meta.kickoff_utc else None
    date_str = d.isoformat() if d else ""
    return f"{_norm(facts.meta.home_team_name)} {_norm(facts.meta.away_team_name)} {date_str}".strip()


def _teams_and_date_match(facts: FlashscoreMatchFacts, context: OpenClawMatchContext) -> bool:
    # Deterministic canonicalization using existing alias index (no fuzzy matching).
    from football_agent.normalizers.team_name_resolver import canonical_team_key, normalize_team_name

    def canon(name: str) -> str:
        return canonical_team_key(normalize_team_name(name or ""))

    home_fs = canon(facts.meta.home_team_name)
    away_fs = canon(facts.meta.away_team_name)

    home_oc = canon(context.meta.query_home_team_normalized or context.meta.query_home_team)
    away_oc = canon(context.meta.query_away_team_normalized or context.meta.query_away_team)

    if not home_fs or not away_fs or not home_oc or not away_oc:
        return False
    if home_fs != home_oc or away_fs != away_oc:
        return False

    # Date: prefer OpenClaw query_date if present; else compare kickoff_utc dates when present.
    fs_date = facts.meta.kickoff_utc.date() if facts.meta.kickoff_utc else None
    oc_date = context.meta.query_date or (context.meta.query_kickoff_utc.date() if context.meta.query_kickoff_utc else None)

    if fs_date and oc_date:
        return fs_date == oc_date

    # If no date data, treat as not linked.
    return False


def _teams_canonical_match_odds(facts: FlashscoreMatchFacts, odds: MatchOddsContext) -> bool:
    from football_agent.normalizers.team_name_resolver import canonical_team_key, normalize_team_name

    def canon(name: str) -> str:
        return canonical_team_key(normalize_team_name(name or ""))

    home_fs = canon(facts.meta.home_team_name)
    away_fs = canon(facts.meta.away_team_name)
    home_od = canon(odds.meta.home_team)
    away_od = canon(odds.meta.away_team)
    if not home_fs or not away_fs or not home_od or not away_od:
        return False
    return home_fs == home_od and away_fs == away_od


def _teams_and_date_match_odds(facts: FlashscoreMatchFacts, odds: MatchOddsContext) -> bool:
    if not _teams_canonical_match_odds(facts, odds):
        return False

    fs_date = facts.meta.kickoff_utc.date() if facts.meta.kickoff_utc else None
    od_date = odds.meta.kickoff_utc.date() if odds.meta.kickoff_utc else None
    if fs_date and od_date:
        return fs_date == od_date
    return False


def _headline_odds_values(odds: Optional[MatchOddsContext]) -> dict:
    if odds is None:
        return {
            "home_win_odds": None,
            "away_win_odds": None,
            "double_chance_1x_odds": None,
            "double_chance_x2_odds": None,
            "btts_yes_odds": None,
            "home_team_to_score_yes_odds": None,
            "away_team_to_score_yes_odds": None,
            "over_1_5_odds": None,
            "under_3_5_odds": None,
        }
    mk = odds.markets
    return {
        "home_win_odds": mk.home_win.odds_value if mk.home_win else None,
        "away_win_odds": mk.away_win.odds_value if mk.away_win else None,
        "double_chance_1x_odds": mk.double_chance_1x.odds_value if mk.double_chance_1x else None,
        "double_chance_x2_odds": mk.double_chance_x2.odds_value if mk.double_chance_x2 else None,
        "btts_yes_odds": mk.btts_yes.odds_value if mk.btts_yes else None,
        "home_team_to_score_yes_odds": mk.home_team_to_score_yes.odds_value if mk.home_team_to_score_yes else None,
        "away_team_to_score_yes_odds": mk.away_team_to_score_yes.odds_value if mk.away_team_to_score_yes else None,
        "over_1_5_odds": mk.over_1_5.odds_value if mk.over_1_5 else None,
        "under_3_5_odds": mk.under_3_5.odds_value if mk.under_3_5 else None,
    }


def _odds_missing_count(odds: Optional[MatchOddsContext]) -> int:
    if odds is None:
        return len(MARKET_FIELDS)
    if odds.provenance and odds.provenance.missing_markets is not None:
        return len(odds.provenance.missing_markets)
    return len([name for name in MARKET_FIELDS if getattr(odds.markets, name) is None])


def _norm(s: str) -> str:
    # Minimal deterministic normalizer (no fuzzy matching):
    # lowercase, trim, collapse spaces, strip common separators.
    return " ".join((s or "").strip().lower().replace("-", " ").split())

