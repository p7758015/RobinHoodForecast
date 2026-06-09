"""
Conservative confidence guardrails for non-league competition contexts.

Does not block analysis — soft penalty + honest warnings only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from football_agent.domain.competition_context import (
    COMPETITION_CONTEXT_LABELS_RU,
    CompetitionContextClass,
)
from football_agent.domain.enums_v2 import ExpressSafetyClass
from football_agent.services.competition_classifier import CompetitionClassification
from football_agent.services.scoring_service_v2 import ScoredRunV2

# Soft confidence penalties (subtracted from overall confidence, capped).
_CONFIDENCE_PENALTY: dict[CompetitionContextClass, float] = {
    CompetitionContextClass.LEAGUE: 0.0,
    CompetitionContextClass.DOMESTIC_CUP: 0.08,
    CompetitionContextClass.INTERNATIONAL_CLUB: 0.10,
    CompetitionContextClass.NATIONAL_TEAM: 0.12,
    CompetitionContextClass.FRIENDLY: 0.15,
    CompetitionContextClass.UNKNOWN: 0.10,
}


@dataclass(frozen=True)
class CompetitionGuardrailResult:
    classification: CompetitionClassification
    guardrail_applied: bool
    confidence_penalty: float
    original_confidence: float
    adjusted_confidence: float
    warnings: List[str] = field(default_factory=list)
    telegram_hint: Optional[str] = None

    def to_debug_dict(self) -> dict:
        return {
            "classification": self.classification.to_debug_dict(),
            "guardrail_applied": self.guardrail_applied,
            "confidence_penalty": self.confidence_penalty,
            "original_confidence": round(self.original_confidence, 4),
            "adjusted_confidence": round(self.adjusted_confidence, 4),
            "warnings": list(self.warnings),
            "telegram_hint": self.telegram_hint,
        }


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _warning_for_category(clf: CompetitionClassification) -> str:
    label = COMPETITION_CONTEXT_LABELS_RU.get(clf.category, clf.category.value)
    if clf.category == CompetitionContextClass.NATIONAL_TEAM:
        return f"competition_guardrail:national_team — клубная форма интерпретируется ограниченно"
    if clf.category == CompetitionContextClass.UNKNOWN:
        return "competition_guardrail:unknown — применена осторожная уверенность"
    return f"competition_guardrail:{clf.category.value} — контекст: {label}"


def _telegram_hint(clf: CompetitionClassification, penalty: float) -> str:
    label = COMPETITION_CONTEXT_LABELS_RU.get(clf.category, clf.category.value)
    if clf.category == CompetitionContextClass.NATIONAL_TEAM:
        return f"Матч сборных — анализ осторожный (−{penalty:.0%} к уверенности)"
    if clf.category == CompetitionContextClass.UNKNOWN:
        return f"Тип турнира неясен — осторожная уверенность (−{penalty:.0%})"
    return f"Контекст: {label} — уверенность скорректирована (−{penalty:.0%})"


def apply_competition_guardrails(
    scored: ScoredRunV2,
    classification: CompetitionClassification,
) -> tuple[ScoredRunV2, CompetitionGuardrailResult]:
    """Apply soft guardrails to scored output; league path unchanged."""
    orig = scored.prediction.overall_confidence_score
    penalty = _CONFIDENCE_PENALTY.get(classification.category, 0.0)
    if penalty <= 0.0 or not classification.requires_guardrail:
        return scored, CompetitionGuardrailResult(
            classification=classification,
            guardrail_applied=False,
            confidence_penalty=0.0,
            original_confidence=orig,
            adjusted_confidence=orig,
        )

    adjusted = _clip01(orig - penalty)
    warn = _warning_for_category(classification)
    express = scored.prediction.express_safety
    express_reasons = list(express.reasons) if express else []
    if warn not in express_reasons:
        express_reasons.append(warn)

    new_express = express.model_copy(
        update={
            "reasons": express_reasons,
            "allow_for_express": False,
            "safety_class": ExpressSafetyClass.EXPRESS_CAUTION,
        },
    ) if express else express

    new_pred = scored.prediction.model_copy(
        update={
            "overall_confidence_score": adjusted,
            "express_safety": new_express,
        },
    )
    new_scored = ScoredRunV2(
        snapshot=scored.snapshot,
        prediction=new_pred,
        build_report=scored.build_report,
        scoring_warnings=list(scored.scoring_warnings) + [warn],
        scored_at_utc=scored.scored_at_utc,
        scorer_name=scored.scorer_name,
        scorer_version=scored.scorer_version,
    )

    return new_scored, CompetitionGuardrailResult(
        classification=classification,
        guardrail_applied=True,
        confidence_penalty=penalty,
        original_confidence=orig,
        adjusted_confidence=adjusted,
        warnings=[warn],
        telegram_hint=_telegram_hint(classification, penalty),
    )
