"""
Builder layer: MergedMatchAnalysisContext → MatchAnalysisSnapshotV2 (+ sidecar BuildReport).

This module is mapping-only:
- no scorer invocation
- no betting recommendations
- no implied probability math
- no bookmaker ranking / line shopping

IDs note (important):
- MatchAnalysisSnapshotV2 requires numeric IDs (match_id, team_id).
- When a trustworthy numeric id is present we use it.
- Otherwise we deterministically synthesize int ids (crc32-based) purely as a compatibility bridge
  for the existing snapshot contract. These synthesized ids are NOT canonical source-of-truth ids.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from football_agent.analysis_merge.models import MergedMatchAnalysisContext
from football_agent.domain.enums_v2 import NewsSeverity, SeasonPhase, TournamentType
from football_agent.domain.models_v2 import (
    CoachContextV2,
    CoachRefV2,
    CompetitionRefV2,
    ConfidenceBreakdownV2,
    H2HContextV2,
    MatchAnalysisSnapshotV2,
    MatchMetaV2,
    NewsContextV2,
    NewsItemV2,
    OddsContextV2,
    OddsMarketV2,
    ScheduleContextV2,
    SquadContextV2,
    TeamContextV2,
    TeamFormBlockV2,
    TeamMotivationBlockV2,
    TeamRefV2,
    TeamScheduleMiniBlockV2,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BuildReport:
    """Sidecar report for merge/builder provenance (kept outside snapshot contract)."""

    merge_warnings: List[str] = field(default_factory=list)
    merge_missing_blocks: List[str] = field(default_factory=list)
    openclaw_link_strategy: str = "unlinked"
    odds_link_strategy: str = "unlinked"

    builder_warnings: List[str] = field(default_factory=list)
    id_generation_notes: Dict[str, str] = field(default_factory=dict)


class MergedSnapshotBuilderV2:
    """
    Public entrypoint:
    - build_from_merged(merged) -> MatchAnalysisSnapshotV2
    - build_with_report(merged) -> (snapshot, BuildReport)
    """

    def build_from_merged(self, merged: MergedMatchAnalysisContext) -> MatchAnalysisSnapshotV2:
        snapshot, _report = self.build_with_report(merged)
        return snapshot

    def build_with_report(self, merged: MergedMatchAnalysisContext) -> Tuple[MatchAnalysisSnapshotV2, BuildReport]:
        report = BuildReport(
            merge_warnings=list(merged.provenance.warnings),
            merge_missing_blocks=list(merged.provenance.missing_blocks),
            openclaw_link_strategy=str(merged.provenance.match_link_strategy),
            odds_link_strategy=str(merged.provenance.odds_link_strategy),
        )

        facts = merged.flashscore_facts
        derived = merged.derived_season_motivation

        match_id, match_id_note = _match_id_int(facts.meta.match_id)
        if match_id_note:
            report.id_generation_notes["match_id"] = match_id_note

        home_team = _team_ref_from_name(facts.meta.home_team_name, side="home", notes=report.id_generation_notes)
        away_team = _team_ref_from_name(facts.meta.away_team_name, side="away", notes=report.id_generation_notes)

        kickoff = facts.meta.kickoff_utc
        if kickoff is None:
            kickoff = _utc_now()
            report.builder_warnings.append("flashscore_kickoff_utc_missing_used_now")

        season = facts.meta.season
        if season is None:
            season = kickoff.year
            report.builder_warnings.append("flashscore_season_missing_used_kickoff_year")

        tournament_type = facts.meta.tournament_type
        try:
            tt: TournamentType = tournament_type  # type: ignore[assignment]
        except Exception:
            tt = TournamentType.LEAGUE_REGULAR

        season_phase = _map_season_phase(derived.season_phase)
        season_progress = _compute_season_progress(facts)

        comp_code = _competition_code_from_flashscore(facts.meta.competition_name)
        comp = CompetitionRefV2(
            competition_code=comp_code,
            name=facts.meta.competition_name,
            country=facts.meta.competition_country,
            tournament_type=tt,
        )

        match_meta = MatchMetaV2(
            match_id=match_id,
            season=int(season),
            competition_name=facts.meta.competition_name,
            competition_code=comp_code,
            tournament_type=tt,
            season_phase=season_phase,
            stage=facts.meta.stage,
            round_number=_safe_int(facts.meta.round),
            match_date_utc=kickoff,
            country=facts.meta.competition_country,
            venue_name=None,
            is_neutral_venue=False,
            home_team=home_team,
            away_team=away_team,
            season_progress=season_progress,
            rounds_played=_safe_int(getattr(facts.season_context_inputs, "matchday_number", None)),
            rounds_remaining=derived.rounds_remaining_after_this_match,
        )

        home_team_ctx = _team_context_from_flashscore(facts, derived, side="home", team_ref=home_team)
        away_team_ctx = _team_context_from_flashscore(facts, derived, side="away", team_ref=away_team)

        home_squad = SquadContextV2(team=home_team, starting_xi_confidence=0.2, line_stability_score=0.5)
        away_squad = SquadContextV2(team=away_team, starting_xi_confidence=0.2, line_stability_score=0.5)

        home_coach_name = _coach_name(merged, side="home")
        away_coach_name = _coach_name(merged, side="away")
        home_coach = CoachContextV2(coach=CoachRefV2(name=home_coach_name), team=home_team)
        away_coach = CoachContextV2(coach=CoachRefV2(name=away_coach_name), team=away_team)

        home_schedule = ScheduleContextV2(team=home_team)
        away_schedule = ScheduleContextV2(team=away_team)

        odds_ctx = _odds_context_from_merged(merged, report)
        news_ctx = _news_context_from_merged(merged)
        h2h_ctx = _h2h_context_from_flashscore(facts)

        snapshot = MatchAnalysisSnapshotV2(
            match_meta=match_meta,
            home_team_context=home_team_ctx,
            away_team_context=away_team_ctx,
            home_squad=home_squad,
            away_squad=away_squad,
            home_coach=home_coach,
            away_coach=away_coach,
            home_schedule=home_schedule,
            away_schedule=away_schedule,
            odds=odds_ctx,
            news_context=news_ctx,
            h2h_context=h2h_ctx,
            confidence=ConfidenceBreakdownV2(),
            source_tags=_source_tags(merged),
        )

        return snapshot, report


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        return int(m.group(0)) if m else None
    return None


def _extract_numeric_id(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _stable_crc32_int(s: str) -> int:
    # crc32 returns unsigned 32-bit; clamp to positive int range for snapshot IDs.
    return int(zlib.crc32((s or "").encode("utf-8")) & 0x7FFFFFFF) or 1


def _match_id_int(match_id_raw: str) -> Tuple[int, str]:
    """
    Prefer real numeric id if present; otherwise synthesize deterministic int id.

    Synthesized ids are compatibility bridges for MatchAnalysisSnapshotV2 only
    and are NOT canonical source-of-truth ids.
    """
    parsed = _extract_numeric_id(match_id_raw or "")
    if parsed is not None:
        return parsed, "used_numeric_substring_from_flashscore_match_id"
    synth = _stable_crc32_int(f"flashscore:{match_id_raw}")
    return synth, "synthesized_crc32_from_flashscore_match_id_for_snapshot_compat_only"


def _team_ref_from_name(name: str, *, side: str, notes: Dict[str, str]) -> TeamRefV2:
    from football_agent.normalizers.team_name_resolver import canonical_team_key, normalize_team_name

    canon = canonical_team_key(normalize_team_name(name or ""))
    if canon:
        tid = _stable_crc32_int(f"team:{canon}")
        notes[f"{side}_team_id"] = "synthesized_crc32_from_canonical_team_key_for_snapshot_compat_only"
    else:
        tid = _stable_crc32_int(f"team_name:{name}")
        notes[f"{side}_team_id"] = "synthesized_crc32_from_team_name_for_snapshot_compat_only"
    return TeamRefV2(team_id=tid, name=name or "Unknown", short_name=name or "Unknown")


def _competition_code_from_flashscore(name: str) -> str:
    # Snapshot requires a competition_code; keep deterministic and simple.
    # Not canonical: just a compatibility code.
    if not name:
        return "FS"
    slug = "_".join(re.sub(r"[^a-zA-Z0-9]+", " ", name).strip().split())
    slug = slug[:20] if slug else "FS"
    return f"FS_{slug}".upper()


def _map_season_phase(phase: Optional[str]) -> Optional[SeasonPhase]:
    if phase == "EARLY":
        return SeasonPhase.EARLY
    if phase == "MID":
        return SeasonPhase.MID
    if phase == "RUN_IN":
        return SeasonPhase.LATE
    if phase == "FINAL_ROUNDS":
        return SeasonPhase.FINAL_RUN_IN
    if phase == "UNKNOWN":
        return SeasonPhase.UNKNOWN
    return None


def _compute_season_progress(facts) -> float:  # noqa: ANN001
    try:
        ctx = facts.season_context_inputs
        if not ctx or not ctx.matchday_number or not ctx.total_matchdays:
            return 0.0
        return max(0.0, min(1.0, float(ctx.matchday_number) / float(ctx.total_matchdays)))
    except Exception:
        return 0.0


def _team_context_from_flashscore(
    facts,
    derived,
    *,
    side: str,
    team_ref: TeamRefV2,
) -> TeamContextV2:  # noqa: ANN001
    standings = facts.standings
    is_home = side == "home"
    pos = getattr(standings, "home_position" if is_home else "away_position", None) if standings else None
    pts = getattr(standings, "home_points" if is_home else "away_points", None) if standings else None
    gd = getattr(standings, "home_goal_difference" if is_home else "away_goal_difference", None) if standings else None

    return TeamContextV2(
        team=team_ref,
        baseline_strength_score=0.5,
        form=TeamFormBlockV2(),
        motivation=TeamMotivationBlockV2(
            league_position=pos,
            points=pts,
            goal_difference=gd,
        ),
        schedule=TeamScheduleMiniBlockV2(),
        availability_score=0.5,
        bench_quality_score=0.5,
        line_stability_score=0.5,
    )


def _odds_context_from_merged(merged: MergedMatchAnalysisContext, report: BuildReport) -> OddsContextV2:
    odds = merged.odds_context
    if odds is None:
        return OddsContextV2(odds_confidence=0.15)

    def market(key: str, name: str, selection: str, q) -> Optional[OddsMarketV2]:  # noqa: ANN001
        if q is None:
            return None
        return OddsMarketV2(
            market_key=key,
            market_name=name,
            selection_name=selection,
            odds=float(q.odds_value),
            bookmaker=q.bookmaker_name,
            source=odds.meta.source,
            collected_at=odds.meta.collected_at_utc,
        )

    mk = odds.markets
    # Note: MatchAnalysisSnapshotV2 OddsContextV2 does not have UNDER_3_5; leave it unmapped.
    mapped = OddsContextV2(
        home_win=market("HOME_WIN", "Match Winner", "Home", mk.home_win),
        draw=None,  # draw intentionally absent in odds v1 contract
        away_win=market("AWAY_WIN", "Match Winner", "Away", mk.away_win),
        home_not_lose=market("HOME_NOT_LOSE", "Double Chance", "Home/Draw", mk.double_chance_1x),
        away_not_lose=market("AWAY_NOT_LOSE", "Double Chance", "Draw/Away", mk.double_chance_x2),
        btts_yes=market("BTTS_YES", "Both Teams Score", "Yes", mk.btts_yes),
        home_team_to_score=market("HOME_TEAM_TO_SCORE", "Home Team To Score", "Yes", mk.home_team_to_score_yes),
        away_team_to_score=market("AWAY_TEAM_TO_SCORE", "Away Team To Score", "Yes", mk.away_team_to_score_yes),
        over_15=market("OVER_1_5", "Goals Over/Under", "Over 1.5", mk.over_1_5),
        odds_confidence=0.5,
    )
    return mapped


def _news_context_from_merged(merged: MergedMatchAnalysisContext) -> NewsContextV2:
    ctx = merged.openclaw_context
    if ctx is None or ctx.news is None:
        return NewsContextV2()

    def items(*lists: Iterable[Any]) -> List[Any]:
        out: List[Any] = []
        for lst in lists:
            out.extend(list(lst or []))
        return out

    raw_items = items(ctx.news.match_news_items, ctx.news.home_news_items, ctx.news.away_news_items)
    major: List[NewsItemV2] = []
    for it in raw_items[:10]:
        major.append(
            NewsItemV2(
                title=str(getattr(it, "title", "")),
                summary=getattr(it, "summary", None),
                severity=NewsSeverity.MEDIUM,
                source=getattr(it, "source_name", None),
                published_at=getattr(it, "published_at", None),
                relevance_score=0.5,
            )
        )

    return NewsContextV2(major_news_items=major)


def _h2h_context_from_flashscore(facts) -> H2HContextV2:  # noqa: ANN001
    h2h = facts.h2h
    if not h2h:
        return H2HContextV2()
    return H2HContextV2(
        team_h2h_total_matches=int(h2h.recent_h2h_matches or 0),
        team_h2h_recent_score=0.0,
        team_h2h_home_away_split=0.0,
        h2h_btts_rate=float(h2h.btts_h2h_rate) if h2h.btts_h2h_rate is not None else 0.5,
        h2h_over25_rate=0.5,
        h2h_context_bias=0.0,
    )


def _coach_name(merged: MergedMatchAnalysisContext, *, side: str) -> str:
    # Prefer Flashscore squad_raw coach names; fallback to OpenClaw coach_context.
    facts = merged.flashscore_facts
    if facts.squad_raw:
        if side == "home" and facts.squad_raw.coach_name_home:
            return str(facts.squad_raw.coach_name_home)
        if side == "away" and facts.squad_raw.coach_name_away:
            return str(facts.squad_raw.coach_name_away)

    oc = merged.openclaw_context
    if oc and oc.coach_context:
        if side == "home" and oc.coach_context.home.coach_name:
            return str(oc.coach_context.home.coach_name)
        if side == "away" and oc.coach_context.away.coach_name:
            return str(oc.coach_context.away.coach_name)
    return "Unknown"


def _source_tags(merged: MergedMatchAnalysisContext) -> List[str]:
    tags = ["analysis_merge", "merged_context_v2", "flashscore"]
    if merged.openclaw_context is not None:
        tags.append("openclaw_context")
        tags.append(f"openclaw_link:{merged.provenance.match_link_strategy}")
    else:
        tags.append("openclaw_context:missing")
    if merged.odds_context is not None:
        tags.append("odds_v1")
        tags.append(f"odds_link:{merged.provenance.odds_link_strategy}")
    else:
        tags.append("odds_v1:missing")
    return tags

