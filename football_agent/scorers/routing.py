"""Tournament-type scorer routing (decision only — no scoring formulas)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import TournamentType
from football_agent.domain.models_v2 import MatchAnalysisSnapshotV2
from football_agent.flashscore.models import FlashscoreMeta
from football_agent.services.competition_classifier import (
    CompetitionClassification,
    classify_competition_meta,
)

RouteKind = Literal["league_full", "non_league_parked", "unknown_parked"]


@dataclass(frozen=True)
class ScorerRoutingDecision:
    route: RouteKind
    tournament_type: TournamentType
    category: CompetitionContextClass
    classification_confidence: str
    league_eligible: bool
    reason: str
    signals: Tuple[str, ...] = ()


def classification_from_snapshot(snapshot: MatchAnalysisSnapshotV2) -> CompetitionClassification:
    """Derive classification from snapshot meta when pipeline did not pass one."""
    meta = snapshot.match_meta
    return classify_competition_meta(
        FlashscoreMeta(
            match_id=str(meta.match_id),
            source_url="",
            competition_name=meta.competition_name,
            competition_country=meta.country,
            tournament_type=meta.tournament_type,
            stage=meta.stage,
            round=str(meta.round_number) if meta.round_number is not None else None,
            home_team_name=meta.home_team.name,
            away_team_name=meta.away_team.name,
        ),
    )


def resolve_scorer_route(
    classification: CompetitionClassification | None,
    *,
    snapshot: MatchAnalysisSnapshotV2 | None = None,
) -> ScorerRoutingDecision:
    """
    Pure routing decision.

    ``classification`` may be None when called from legacy paths — then inferred
    from ``snapshot`` when provided, otherwise ``unknown_parked``.
    """
    clf = classification
    if clf is None and snapshot is not None:
        clf = classification_from_snapshot(snapshot)
    if clf is None:
        return ScorerRoutingDecision(
            route="unknown_parked",
            tournament_type=TournamentType.UNKNOWN,
            category=CompetitionContextClass.UNKNOWN,
            classification_confidence="low",
            league_eligible=False,
            reason="parked:unknown",
            signals=("classification_missing",),
        )

    signals = tuple(clf.signals or ())
    if clf.is_league_eligible:
        return ScorerRoutingDecision(
            route="league_full",
            tournament_type=clf.tournament_type,
            category=clf.category,
            classification_confidence=clf.confidence,
            league_eligible=True,
            reason="league_full:high_confidence_league",
            signals=signals,
        )

    if clf.category == CompetitionContextClass.UNKNOWN:
        return ScorerRoutingDecision(
            route="unknown_parked",
            tournament_type=clf.tournament_type,
            category=clf.category,
            classification_confidence=clf.confidence,
            league_eligible=False,
            reason="parked:unknown",
            signals=signals,
        )

    family = clf.category.value
    return ScorerRoutingDecision(
        route="non_league_parked",
        tournament_type=clf.tournament_type,
        category=clf.category,
        classification_confidence=clf.confidence,
        league_eligible=False,
        reason=f"parked:{family}",
        signals=signals,
    )
