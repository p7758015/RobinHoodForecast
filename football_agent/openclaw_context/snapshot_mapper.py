"""
Map OpenClawMatchContext → blueprint-aligned snapshot fields (scorer-critical blocks).

OpenClaw is the primary enrichment source; this module is backend-agnostic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from football_agent.domain.enums_v2 import AvailabilityStatus, NewsSeverity, PlayerImportance
from football_agent.domain.models_v2 import (
    NewsContextV2,
    NewsItemV2,
    PlayerAvailabilityV2,
    PlayerRefV2,
    ScheduleContextV2,
    SquadContextV2,
)
from football_agent.openclaw_context.models import (
    OpenClawCoachSideContext,
    OpenClawFatigueScheduleSide,
    OpenClawMatchContext,
    OpenClawMotivationNarrativeSide,
    OpenClawPlayerContextItem,
    OpenClawSquadSideContext,
)

_RELIABILITY_SCORE = {"HIGH": 0.85, "MEDIUM": 0.6, "LOW": 0.35, "UNKNOWN": 0.25}
_STATUS_MAP = {
    "OUT": AvailabilityStatus.INJURED,
    "INJURED": AvailabilityStatus.INJURED,
    "SUSPENDED": AvailabilityStatus.SUSPENDED,
    "DOUBTFUL": AvailabilityStatus.DOUBTFUL,
    "RETURNING": AvailabilityStatus.AVAILABLE,
    "ROTATION_RISK": AvailabilityStatus.DOUBTFUL,
    "UNKNOWN": AvailabilityStatus.UNKNOWN,
}
_RISK_LEVEL_SCORE = {"LOW": 0.2, "MEDIUM": 0.5, "HIGH": 0.75, "UNKNOWN": 0.35}
_FIRST_MATCH_RE = re.compile(
    r"\bfirst\s+(?:game|match|fixture)\b|\bdebut\b|\bmaiden\s+match\b",
    re.I,
)
_ROTATION_RE = re.compile(r"\brotat|\bbench|\brest\b|\blineup\s+change", re.I)
_FATIGUE_RE = re.compile(r"\bfatigue|\btired|\bcongest|\bshort\s+rest|\btravel", re.I)
_LOCKER_RE = re.compile(r"\blocker|\bdressing|\bunrest|\bconflict|\btension", re.I)


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _reliability(conf: Optional[str]) -> float:
    return _RELIABILITY_SCORE.get(str(conf or "UNKNOWN").upper(), 0.25)


def _risk_level(level: Optional[str]) -> float:
    return _RISK_LEVEL_SCORE.get(str(level or "UNKNOWN").upper(), 0.35)


def openclaw_block_status(ctx: Optional[OpenClawMatchContext]) -> Dict[str, str]:
    """Per-domain status for debug reports."""
    if ctx is None:
        return {
            "squads": "missing",
            "coaches": "missing",
            "news": "missing",
            "motivation": "missing",
            "schedule": "missing",
        }
    prov = ctx.provenance
    present = set(prov.blocks_present or [])

    def _status(block: str) -> str:
        if block in present:
            return "partial" if block in (prov.missing_blocks or []) else "ok"
        return "missing"

    return {
        "squads": _status("squad_context"),
        "coaches": _status("coach_context"),
        "news": _status("news"),
        "motivation": _status("motivation_narrative"),
        "schedule": _status("fatigue_schedule_context"),
        "backend": prov.backend_name,
        "blocks_present": list(present),
        "missing_blocks": list(prov.missing_blocks or []),
    }


def _player_from_oc(item: OpenClawPlayerContextItem) -> PlayerAvailabilityV2:
    status = _STATUS_MAP.get(str(item.status or "UNKNOWN").upper(), AvailabilityStatus.UNKNOWN)
    importance = PlayerImportance.HIGH if "key" in (item.reason or "").lower() else PlayerImportance.MEDIUM
    return PlayerAvailabilityV2(
        player=PlayerRefV2(name=item.player_name or "unknown"),
        status=status,
        importance=importance,
        reason=item.reason or item.expected_impact,
        confidence=_reliability(item.confidence),
    )


def apply_openclaw_squad_overlay(
    squad: SquadContextV2,
    oc_side: Optional[OpenClawSquadSideContext],
) -> SquadContextV2:
    """Merge OpenClaw squad block into SquadContextV2 (additive, fail-soft)."""
    if oc_side is None:
        return squad

    missing = list(squad.missing_players)
    doubtful = list(squad.doubtful_players)
    suspended = list(squad.suspended_players)
    seen = {p.player.name.lower() for p in missing + doubtful + suspended}

    for item in list(oc_side.missing_players_context or []):
        entry = _player_from_oc(item)
        key = entry.player.name.lower()
        if key in seen:
            continue
        seen.add(key)
        if entry.status == AvailabilityStatus.SUSPENDED:
            suspended.append(entry)
        elif entry.status == AvailabilityStatus.DOUBTFUL:
            doubtful.append(entry)
        else:
            missing.append(entry)

    for item in list(oc_side.returning_players_context or []):
        entry = _player_from_oc(item)
        entry.status = AvailabilityStatus.AVAILABLE
        if entry.player.name.lower() not in seen:
            doubtful.append(entry)

    rotation_risk = _risk_level(oc_side.rotation_risk_level)
    depth_risk = _risk_level(oc_side.depth_risk_level)
    uncertainty_notes = list(oc_side.lineup_uncertainty_notes or [])
    has_uncertainty = bool(uncertainty_notes) or rotation_risk >= 0.45

    missing_count = len(missing)
    key_missing = sum(
        1 for p in missing + suspended + doubtful
        if p.importance in (PlayerImportance.HIGH, PlayerImportance.CRITICAL)
    )

    xi_conf = squad.starting_xi_confidence
    if has_uncertainty:
        xi_conf = min(xi_conf, _clip01(0.55 - rotation_risk * 0.2))
    if missing_count > 0:
        xi_conf = min(xi_conf, _clip01(0.65 - 0.06 * min(missing_count, 5)))
    if oc_side.high_confidence_count and oc_side.high_confidence_count > 0:
        xi_conf = max(xi_conf, 0.45)

    line_stab = squad.line_stability_score
    if rotation_risk >= 0.5:
        line_stab = min(line_stab, 0.45)
    elif depth_risk >= 0.5:
        line_stab = min(line_stab, 0.5)

    avail = _clip01(
        0.35 * xi_conf + 0.35 * line_stab + 0.3 * (1.0 - depth_risk * 0.5),
    )
    key_impact = _clip01(squad.key_absence_impact_score + 0.08 * key_missing)

    return squad.model_copy(
        update={
            "missing_players": missing,
            "doubtful_players": doubtful,
            "suspended_players": suspended,
            "missing_players_count": missing_count,
            "missing_key_players_count": key_missing,
            "starting_xi_confidence": _clip01(xi_conf),
            "line_stability_score": _clip01(line_stab),
            "availability_score": avail,
            "key_absence_impact_score": key_impact,
        },
    )


def news_context_from_openclaw(ctx: OpenClawMatchContext) -> NewsContextV2:
    """Build NewsContextV2 from OpenClaw news + motivation + squad/fatigue signals."""
    major: List[NewsItemV2] = []
    priority: List[str] = []
    rotation: List[str] = []
    locker: List[str] = []
    fatigue: List[str] = []
    returnees: List[str] = []

    if ctx.news:
        for it in list(ctx.news.match_news_items or [])[:8]:
            rel = _reliability(it.reliability_level)
            major.append(
                NewsItemV2(
                    title=it.title,
                    summary=it.summary,
                    severity=NewsSeverity.HIGH if rel >= 0.7 else NewsSeverity.MEDIUM,
                    source=it.source_name or "openclaw",
                    published_at=it.published_at,
                    relevance_score=rel,
                ),
            )
            blob = f"{it.title} {it.summary or ''}".lower()
            if _LOCKER_RE.search(blob):
                locker.append(it.title)
            if _ROTATION_RE.search(blob):
                rotation.append(it.title)
            if _FATIGUE_RE.search(blob):
                fatigue.append(it.title)

    mot = ctx.motivation_narrative
    if mot:
        for side_obj in (mot.home, mot.away, mot.matchwide):
            if side_obj is None:
                continue
            for field in (
                side_obj.primary_objective_summary,
                side_obj.must_win_narrative,
                side_obj.public_narrative_summary,
                side_obj.pressure_summary,
            ):
                if field:
                    priority.append(str(field)[:120])

    sq = ctx.squad_context
    if sq:
        for side_obj in (sq.home, sq.away):
            if side_obj is None:
                continue
            rotation.extend(list(side_obj.expected_rotation_notes or [])[:2])
            for item in list(side_obj.returning_players_context or [])[:3]:
                returnees.append(item.player_name)

    fat = ctx.fatigue_schedule_context
    if fat:
        for side_obj in (fat.home, fat.away):
            if side_obj is None:
                continue
            for field in (
                side_obj.fatigue_summary,
                side_obj.rotation_expectation_summary,
                side_obj.sandwich_match_risk_summary,
                side_obj.post_europe_risk_summary,
            ):
                if field:
                    fatigue.append(str(field)[:120])

    risk = 0.25
    if locker:
        risk += 0.15
    if rotation:
        risk += 0.1
    if fatigue:
        risk += 0.08
    if major:
        risk += 0.05 * min(3, len(major))
    risk = _clip01(risk)

    return NewsContextV2(
        major_news_items=major,
        locker_room_issues=locker[:5],
        important_returnees=returnees[:5],
        priority_signals=priority[:5],
        rotation_signals=rotation[:5],
        fatigue_signals=fatigue[:5],
        news_risk_score=risk,
    )


def schedule_overlay_from_openclaw(
    schedule: ScheduleContextV2,
    fat_side: Optional[OpenClawFatigueScheduleSide],
    mot_side: Optional[OpenClawMotivationNarrativeSide],
) -> ScheduleContextV2:
    """Apply OpenClaw fatigue/motivation hints to schedule context."""
    rotation_risk = schedule.rotation_risk_score
    pre_big = schedule.pre_big_match_preservation_risk
    post_big = schedule.post_big_match_relaxation_risk
    congestion = schedule.fixture_congestion_score

    if fat_side is not None:
        blob = " ".join(
            filter(
                None,
                [
                    fat_side.fatigue_summary,
                    fat_side.rotation_expectation_summary,
                    fat_side.sandwich_match_risk_summary,
                    fat_side.post_europe_risk_summary,
                    fat_side.travel_summary,
                ],
            ),
        ).lower()
        if _FATIGUE_RE.search(blob) or "congest" in blob:
            congestion = max(congestion, 0.55)
        if _ROTATION_RE.search(blob):
            rotation_risk = max(rotation_risk, 0.5)
        if "big match" in blob or "preserve" in blob or "rotate" in blob:
            pre_big = max(pre_big, 0.35)
        if "after" in blob and ("europe" in blob or "cup" in blob):
            post_big = max(post_big, 0.3)

    if mot_side is not None and mot_side.distraction_risk_summary:
        if "big" in mot_side.distraction_risk_summary.lower():
            pre_big = max(pre_big, 0.25)

    return schedule.model_copy(
        update={
            "rotation_risk_score": _clip01(rotation_risk),
            "pre_big_match_preservation_risk": _clip01(pre_big),
            "post_big_match_relaxation_risk": _clip01(post_big),
            "fixture_congestion_score": _clip01(congestion),
        },
    )


def coach_tenure_from_openclaw(
    oc_side: Optional[OpenClawCoachSideContext],
) -> Tuple[Optional[int], bool, bool]:
    """Returns (days_in_charge, is_first_match, recent_change)."""
    if oc_side is None:
        return None, False, bool(oc_side and oc_side.recent_change_flag)

    days: Optional[int] = None
    if oc_side.tenure_summary:
        m = re.search(r"(\d+)\s+days?", oc_side.tenure_summary, re.I)
        if m:
            days = int(m.group(1))

    is_first = bool(oc_side.recent_change_flag)
    blob = " ".join(
        filter(None, [oc_side.tenure_summary, oc_side.pressure_summary, oc_side.influence_summary]),
    )
    if _FIRST_MATCH_RE.search(blob or ""):
        is_first = True

    return days, is_first, bool(oc_side.recent_change_flag)


def openclaw_confidence_scores(ctx: Optional[OpenClawMatchContext]) -> Dict[str, float]:
    """Block-level confidence estimates from OpenClaw provenance."""
    if ctx is None:
        return {"squads": 0.15, "coaches": 0.15, "news": 0.15, "schedule": 0.15}
    prov = ctx.provenance
    present = set(prov.blocks_present or [])
    base = 0.2
    if "direct_gateway" in prov.backend_name or "inprocess" in prov.backend_name:
        base = 0.42
    if prov.backend_name == "openclaw_bridge":
        base = 0.55

    def _block_conf(name: str, has_signal: bool) -> float:
        if name not in present:
            return 0.15
        score = base if has_signal else base * 0.7
        if name in (prov.missing_blocks or []):
            score *= 0.75
        return _clip01(score)

    sq = ctx.squad_context
    cc = ctx.coach_context
    has_squad = bool(
        sq
        and (
            sq.home.missing_players_context
            or sq.away.missing_players_context
            or sq.home.lineup_uncertainty_notes
            or sq.away.lineup_uncertainty_notes
        ),
    )
    has_coach = bool(
        cc and (cc.home.coach_name or cc.away.coach_name or cc.home.influence_summary),
    )
    has_news = bool(ctx.news and ctx.news.match_news_items)
    has_sched = bool(ctx.fatigue_schedule_context)

    return {
        "squads": _block_conf("squad_context", has_squad),
        "coaches": _block_conf("coach_context", has_coach),
        "news": _block_conf("news", has_news),
        "schedule": _block_conf("fatigue_schedule_context", has_sched),
    }
