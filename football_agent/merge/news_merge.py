"""Additive merge of Brave news enrichment into merged context (fail-soft)."""

from __future__ import annotations

from copy import deepcopy
from typing import List, Optional

from football_agent import config
from football_agent.analysis_merge.models import MergedMatchAnalysisContext, MergeProvenance
from football_agent.news_context.models import MatchNewsContext
from football_agent.openclaw_context.models import OpenClawCoachContext, OpenClawCoachMatchupContext, OpenClawCoachSideContext
from football_agent.services.openclaw_news_enrichment import brave_coach_context_enabled


def merge_news_into_merged_context(
    merged: MergedMatchAnalysisContext,
    news: Optional[MatchNewsContext],
) -> MergedMatchAnalysisContext:
    """
    Attach news enrichment block without modifying Flashscore factual fields.

    Optionally augments OpenClaw coach_context summaries when override flags are false.
    """
    if news is None:
        return merged

    prov = deepcopy(merged.provenance)
    blocks = list(prov.blocks_present or [])
    missing = list(prov.missing_blocks or [])
    warnings = list(prov.warnings or [])

    blocks.append("news_enrichment")
    if "news_enrichment" in missing:
        missing.remove("news_enrichment")
    blocks.append("news_context")
    if "news_context" in missing:
        missing.remove("news_context")
    if news.warnings:
        warnings.extend(news.warnings)
    if news.source_count == 0 and "brave_news_sparse" not in warnings:
        warnings.append("brave_news_sparse")

    headline = merged.headline.model_copy(
        update={"openclaw_context_present": merged.headline.openclaw_context_present or bool(news.sources)},
    )

    openclaw_ctx = merged.openclaw_context
    if openclaw_ctx is not None and brave_coach_context_enabled():
        openclaw_ctx = _augment_openclaw_coach(openclaw_ctx, news)

    return merged.model_copy(
        update={
            "headline": headline,
            "openclaw_context": openclaw_ctx,
            "news_context": news,
            "provenance": prov.model_copy(
                update={
                    "blocks_present": blocks,
                    "missing_blocks": missing,
                    "warnings": warnings,
                },
            ),
        },
    )


def _augment_openclaw_coach(openclaw_ctx, news: MatchNewsContext):  # noqa: ANN001
    """Fill OpenClaw coach summaries from Brave block without renaming if disallowed."""
    coach = news.coach
    if openclaw_ctx.coach_context is None:
        openclaw_ctx = openclaw_ctx.model_copy(
            update={
                "coach_context": OpenClawCoachContext(
                    home=OpenClawCoachSideContext(),
                    away=OpenClawCoachSideContext(),
                    matchup=OpenClawCoachMatchupContext(),
                ),
            },
        )
    cc = openclaw_ctx.coach_context
    home = cc.home.model_copy()
    away = cc.away.model_copy()
    matchup = cc.matchup.model_copy()

    if coach.home_coach_name and (
        config.OPENCLAW_CAN_OVERRIDE_COACH_NAMES or not home.coach_name
    ):
        home.coach_name = coach.home_coach_name
    if coach.away_coach_name and (
        config.OPENCLAW_CAN_OVERRIDE_COACH_NAMES or not away.coach_name
    ):
        away.coach_name = coach.away_coach_name

    if coach.home_coach_rotation_signal and not home.influence_summary:
        home.influence_summary = coach.home_coach_rotation_signal
    if coach.away_coach_rotation_signal and not away.influence_summary:
        away.influence_summary = coach.away_coach_rotation_signal
    if coach.home_coach_morale_signal and not home.pressure_summary:
        home.pressure_summary = coach.home_coach_morale_signal
    if coach.away_coach_morale_signal and not away.pressure_summary:
        away.pressure_summary = coach.away_coach_morale_signal

    if coach.coach_h2h_recent_summary and not matchup.coach_vs_coach_summary:
        matchup.coach_vs_coach_summary = coach.coach_h2h_recent_summary
        matchup.confidence = coach.coach_h2h_confidence

    updated = cc.model_copy(update={"home": home, "away": away, "matchup": matchup})
    return openclaw_ctx.model_copy(update={"coach_context": updated})
