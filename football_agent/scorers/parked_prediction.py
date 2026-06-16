"""Analysis-only prediction when league scorer is not applied."""

from __future__ import annotations

from typing import List, Optional

from football_agent.domain.competition_context import COMPETITION_CONTEXT_LABELS_RU
from football_agent.domain.enums_v2 import ExpressSafetyClass
from football_agent.domain.models_v2 import (
    ExpressScreeningV2,
    MatchAnalysisSnapshotV2,
    MatchPredictionResultV2,
    OddsContextV2,
    ParkedAnalysisContextV2,
    ParkedRouteKind,
    TeamScoringResultV2,
)
from football_agent.scorers.routing import ScorerRoutingDecision


def _count_odds_markets(odds: Optional[OddsContextV2]) -> int:
    if odds is None:
        return 0
    fields = (
        "home_win",
        "draw",
        "away_win",
        "home_not_lose",
        "away_not_lose",
        "btts_yes",
        "home_team_to_score",
        "away_team_to_score",
        "over_15",
    )
    return sum(1 for name in fields if getattr(odds, name, None) is not None)


def _standings_available(snapshot: MatchAnalysisSnapshotV2) -> bool:
    for ctx in (snapshot.home_team_context, snapshot.away_team_context):
        mot = ctx.motivation
        if mot.league_position is not None or mot.points is not None:
            return True
    return False


def _news_available(snapshot: MatchAnalysisSnapshotV2) -> bool:
    news = snapshot.news_context
    if news is None:
        return False
    return bool(news.major_news_items or news.priority_signals or news.rotation_signals)


def _data_quality_note(confidence: float, completeness: float) -> Optional[str]:
    if confidence < 0.35 and completeness < 0.35:
        return "very_low_data"
    if confidence < 0.45 or completeness < 0.45:
        return "low_data"
    if confidence < 0.6 or completeness < 0.6:
        return "partial_data"
    return None


def _parked_route(decision: ScorerRoutingDecision) -> ParkedRouteKind:
    if decision.route == "unknown_parked":
        return "unknown_parked"
    return "non_league_parked"


def build_parked_context(
    snapshot: MatchAnalysisSnapshotV2,
    decision: ScorerRoutingDecision,
) -> ParkedAnalysisContextV2:
    conf = snapshot.confidence
    snapshot_conf = float(conf.overall_confidence_score)
    snapshot_compl = float(conf.overall_completeness_score)
    odds_count = _count_odds_markets(snapshot.odds)
    book_odds = odds_count > 0 and (snapshot.odds.odds_confidence or 0) > 0.15

    return ParkedAnalysisContextV2(
        route=_parked_route(decision),
        tournament_type=decision.tournament_type,
        category=decision.category.value,
        reason=decision.reason,
        book_odds_available=book_odds,
        book_odds_markets_count=odds_count,
        news_available=_news_available(snapshot),
        standings_available=_standings_available(snapshot),
        snapshot_confidence=snapshot_conf,
        snapshot_completeness=snapshot_compl,
        can_build_express=False,
        data_quality_note=_data_quality_note(snapshot_conf, snapshot_compl),
    )


def build_parked_summary(
    snapshot: MatchAnalysisSnapshotV2,
    decision: ScorerRoutingDecision,
    parked: ParkedAnalysisContextV2,
) -> str:
    label = COMPETITION_CONTEXT_LABELS_RU.get(decision.category, decision.category.value)
    comp = snapshot.match_meta.competition_name or snapshot.match_meta.competition_code

    lines: List[str] = [
        f"Матч «{comp}» определён как {label} ({decision.tournament_type.value}).",
        "League scoring не применяется — для этого типа турнира активная scoring-ветка ещё не реализована.",
        "Доступен analysis-only разбор собранного контекста.",
    ]

    collected: List[str] = []
    if parked.standings_available:
        collected.append("таблица/позиции")
    if parked.book_odds_available:
        collected.append(f"линия букмекера ({parked.book_odds_markets_count} рынков)")
    if parked.news_available:
        collected.append("новости/контекст")
    if snapshot.h2h_context and snapshot.h2h_context.team_h2h_total_matches > 0:
        collected.append("личные встречи")
    if collected:
        lines.append("Собрано: " + ", ".join(collected) + ".")

    if parked.data_quality_note in ("very_low_data", "low_data"):
        lines.append(
            f"Низкая полнота данных (уверенность {parked.snapshot_confidence:.0%}, "
            f"completeness {parked.snapshot_completeness:.0%}) — выводы ограничены."
        )
    elif parked.data_quality_note == "partial_data":
        lines.append(
            f"Данные частичные (уверенность {parked.snapshot_confidence:.0%})."
        )

    if parked.book_odds_available:
        lines.append(
            "Линия букмекера отражена как справочный контекст, не как кодовый прогноз."
        )

    return " ".join(lines)


def build_parked_prediction(
    snapshot: MatchAnalysisSnapshotV2,
    decision: ScorerRoutingDecision,
) -> MatchPredictionResultV2:
    home = snapshot.match_meta.home_team
    away = snapshot.match_meta.away_team
    parked = build_parked_context(snapshot, decision)
    summary = build_parked_summary(snapshot, decision, parked)
    snapshot_conf = parked.snapshot_confidence

    express_reasons = [
        decision.reason,
        "analysis_only_mode",
        f"route:{parked.route}",
        "express_not_available_for_parked",
    ]
    if parked.data_quality_note:
        express_reasons.append(f"data_quality:{parked.data_quality_note}")

    return MatchPredictionResultV2(
        match_meta=snapshot.match_meta,
        home_scoring=TeamScoringResultV2(team=home),
        away_scoring=TeamScoringResultV2(team=away),
        market_predictions=[],
        best_market=None,
        express_safety=ExpressScreeningV2(
            safety_class=ExpressSafetyClass.EXPRESS_CAUTION,
            allow_for_express=False,
            reasons=express_reasons,
        ),
        prediction_summary=summary,
        overall_confidence_score=snapshot_conf,
        analysis_mode="analysis_only",
        prediction_mode="parked_analysis_only",
        parked_context=parked,
    )
