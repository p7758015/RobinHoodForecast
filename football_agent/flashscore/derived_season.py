"""Derived season/table motivation logic on top of FlashscoreMatchFacts.

This is an early, league-focused version:
- season_phase: EARLY / MID / RUN_IN / FINAL_ROUNDS / UNKNOWN
- target_band: TITLE / EUROPE / MIDTABLE / RELEGATION / UNKNOWN
- urgency_level: LOW / MEDIUM / HIGH / CRITICAL / UNKNOWN

Rules are intentionally simple and transparent and may be extended later
for lower leagues, playoffs, cups, and group stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from football_agent.domain.enums_v2 import TournamentType
from football_agent.flashscore.models import FlashscoreMatchFacts

SeasonPhase = Literal["EARLY", "MID", "RUN_IN", "FINAL_ROUNDS", "UNKNOWN"]
TargetBand = Literal["TITLE", "EUROPE", "MIDTABLE", "RELEGATION", "UNKNOWN"]
UrgencyLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]


@dataclass
class LeagueTableMotivationContext:
    season_phase: SeasonPhase
    rounds_remaining_after_this_match: Optional[int]

    # Primary points-based gaps (preferred when thresholds exist).
    gap_to_title_points: Optional[int]
    gap_to_europe_points: Optional[int]
    gap_to_relegation_safety_points: Optional[int]

    # Secondary / auxiliary positional gaps for debug only (not used for alive flags).
    aux_gap_to_title_positions: Optional[int]
    aux_gap_to_europe_positions: Optional[int]
    aux_gap_to_relegation_line_positions: Optional[int]

    home_mathematical_title_alive: Optional[bool]
    away_mathematical_title_alive: Optional[bool]

    home_mathematical_europe_alive: Optional[bool]
    away_mathematical_europe_alive: Optional[bool]

    home_mathematical_relegation_risk_alive: Optional[bool]
    away_mathematical_relegation_risk_alive: Optional[bool]

    home_target_band: TargetBand
    away_target_band: TargetBand

    urgency_level_home: UrgencyLevel
    urgency_level_away: UrgencyLevel

    # Legacy/compat fields (deprecated): keep for now; prefer gap_to_*_points above.
    points_gap_home_to_title: Optional[int]
    points_gap_away_to_title: Optional[int]
    points_gap_home_to_europe: Optional[int]
    points_gap_away_to_europe: Optional[int]
    points_gap_home_to_relegation_line: Optional[int]
    points_gap_away_to_relegation_line: Optional[int]

    derivation_warnings: List[str]


def derive_season_motivation(facts: FlashscoreMatchFacts) -> LeagueTableMotivationContext:
    meta = facts.meta
    standings = facts.standings
    season_ctx = facts.season_context_inputs

    warnings: List[str] = []

    # Non-league: we do not apply full league motivation math.
    if meta.tournament_type != TournamentType.LEAGUE_REGULAR:
        warnings.append(
            f"tournament_type={meta.tournament_type} is not LEAGUE_REGULAR; "
            "league motivation context may not apply fully."
        )

    matchday = season_ctx.matchday_number if season_ctx else None
    total = season_ctx.total_matchdays if season_ctx else None
    rounds_remaining = (
        season_ctx.rounds_remaining_after_this_match if season_ctx else None
    )

    season_phase = _classify_season_phase(matchday, total, meta.tournament_type, warnings)

    (
        aux_gap_home_title_pos,
        aux_gap_away_title_pos,
        aux_gap_home_euro_pos,
        aux_gap_away_euro_pos,
        aux_gap_home_releg_pos,
        aux_gap_away_releg_pos,
        gap_home_title_pts,
        gap_away_title_pts,
        gap_home_euro_pts,
        gap_away_euro_pts,
        gap_home_safe_pts,
        gap_away_safe_pts,
        math_title_home_alive,
        math_title_away_alive,
        math_euro_home_alive,
        math_euro_away_alive,
        math_releg_home_alive,
        math_releg_away_alive,
    ) = _compute_points_gaps_and_flags(standings, season_ctx, warnings)

    home_band = _classify_target_band(
        "home",
        standings,
        season_ctx,
        aux_gap_home_title_pos,
        aux_gap_home_euro_pos,
        aux_gap_home_releg_pos,
    )
    away_band = _classify_target_band(
        "away",
        standings,
        season_ctx,
        aux_gap_away_title_pos,
        aux_gap_away_euro_pos,
        aux_gap_away_releg_pos,
    )

    urg_home = _classify_urgency(
        band=home_band,
        rounds_remaining=rounds_remaining,
        gap_to_target=min(
            _positive_or_inf(aux_gap_home_title_pos),
            _positive_or_inf(aux_gap_home_euro_pos),
            _positive_or_inf(aux_gap_home_releg_pos),
        ),
    )
    urg_away = _classify_urgency(
        band=away_band,
        rounds_remaining=rounds_remaining,
        gap_to_target=min(
            _positive_or_inf(aux_gap_away_title_pos),
            _positive_or_inf(aux_gap_away_euro_pos),
            _positive_or_inf(aux_gap_away_releg_pos),
        ),
    )

    return LeagueTableMotivationContext(
        season_phase=season_phase,
        rounds_remaining_after_this_match=rounds_remaining,
        gap_to_title_points=gap_home_title_pts,
        gap_to_europe_points=gap_home_euro_pts,
        gap_to_relegation_safety_points=gap_home_safe_pts,
        aux_gap_to_title_positions=aux_gap_home_title_pos,
        aux_gap_to_europe_positions=aux_gap_home_euro_pos,
        aux_gap_to_relegation_line_positions=aux_gap_home_releg_pos,
        home_mathematical_title_alive=math_title_home_alive,
        away_mathematical_title_alive=math_title_away_alive,
        home_mathematical_europe_alive=math_euro_home_alive,
        away_mathematical_europe_alive=math_euro_away_alive,
        home_mathematical_relegation_risk_alive=math_releg_home_alive,
        away_mathematical_relegation_risk_alive=math_releg_away_alive,
        home_target_band=home_band,
        away_target_band=away_band,
        urgency_level_home=urg_home,
        urgency_level_away=urg_away,
        points_gap_home_to_title=aux_gap_home_title_pos,
        points_gap_away_to_title=aux_gap_away_title_pos,
        points_gap_home_to_europe=aux_gap_home_euro_pos,
        points_gap_away_to_europe=aux_gap_away_euro_pos,
        points_gap_home_to_relegation_line=aux_gap_home_releg_pos,
        points_gap_away_to_relegation_line=aux_gap_away_releg_pos,
        derivation_warnings=warnings,
    )


def _classify_season_phase(
    matchday: Optional[int],
    total_matchdays: Optional[int],
    tournament_type: TournamentType,
    warnings: List[str],
) -> SeasonPhase:
    """
    Phase rules (only for LEAGUE_REGULAR):

    - UNKNOWN: missing matchday/total_matchdays OR non-league tournament
    - EARLY:  played_fraction < 0.25
    - MID:    0.25 <= played_fraction < 0.70
    - RUN_IN: 0.70 <= played_fraction < 0.90
    - FINAL_ROUNDS: played_fraction >= 0.90

    where played_fraction = matchday / total_matchdays (clamped 0..1).
    """

    if tournament_type != TournamentType.LEAGUE_REGULAR:
        return "UNKNOWN"
    if not matchday or not total_matchdays or total_matchdays <= 0:
        warnings.append("Cannot compute season_phase: missing matchday_number/total_matchdays.")
        return "UNKNOWN"

    played_fraction = max(0.0, min(1.0, float(matchday) / float(total_matchdays)))
    if played_fraction < 0.25:
        return "EARLY"
    if played_fraction < 0.70:
        return "MID"
    if played_fraction < 0.90:
        return "RUN_IN"
    return "FINAL_ROUNDS"


def _compute_points_gaps_and_flags(
    standings,
    season_ctx,
    warnings: List[str],
):
    """
    Compute points-based gaps (primary) + auxiliary positional gaps + mathematical-alive flags.

    Primary gaps are points-based and computed only when we have explicit thresholds:
    - title_leader_points
    - ucl_cutoff_points
    - relegation_safety_points

    If thresholds are missing, gaps are set to None and a human-readable warning is added.
    Positional gaps remain available as auxiliary debug fields only and are NOT used for
    mathematical alive flags.
    """

    from football_agent.flashscore.models import FlashscoreStandings, FlashscoreSeasonContextInputs

    if not isinstance(standings, FlashscoreStandings):
        warnings.append("Standings missing; cannot compute points gaps.")
        return (None,) * 18

    if not isinstance(season_ctx, FlashscoreSeasonContextInputs):
        season_ctx = None

    hp = standings.home_points
    ap = standings.away_points
    if hp is None or ap is None:
        warnings.append("missing team_points (home_points/away_points)")
        return (None,) * 18

    euro_cut_pos = None
    releg_cut_pos = None
    title_leader_points = None
    ucl_cutoff_points = None
    relegation_safety_points = None
    if season_ctx:
        euro_cut_pos = season_ctx.table_neighbors.get("ucl_cutoff_pos")
        releg_cut_pos = season_ctx.table_neighbors.get("relegation_cutoff_pos")
        title_leader_points = season_ctx.table_neighbors.get("title_leader_points") or season_ctx.relevant_thresholds.get(
            "title_leader_points"
        )
        ucl_cutoff_points = season_ctx.table_neighbors.get("ucl_cutoff_points") or season_ctx.relevant_thresholds.get(
            "ucl_cutoff_points"
        )
        relegation_safety_points = season_ctx.table_neighbors.get(
            "relegation_safety_points"
        ) or season_ctx.relevant_thresholds.get("relegation_safety_points")

    home_pos = standings.home_position
    away_pos = standings.away_position

    def pos_gap(pos: Optional[int], target_pos: Optional[int]) -> Optional[int]:
        if pos is None or target_pos is None:
            return None
        return pos - target_pos

    # Auxiliary positional gaps (debug only)
    aux_gap_home_title_pos = pos_gap(home_pos, 1)
    aux_gap_away_title_pos = pos_gap(away_pos, 1)
    aux_gap_home_euro_pos = pos_gap(home_pos, euro_cut_pos) if euro_cut_pos is not None else None
    aux_gap_away_euro_pos = pos_gap(away_pos, euro_cut_pos) if euro_cut_pos is not None else None
    aux_gap_home_releg_pos = pos_gap(releg_cut_pos, home_pos) if releg_cut_pos is not None else None
    aux_gap_away_releg_pos = pos_gap(releg_cut_pos, away_pos) if releg_cut_pos is not None else None

    # Primary points gaps (threshold-based only)
    gap_home_title_pts = None
    gap_away_title_pts = None
    gap_home_euro_pts = None
    gap_away_euro_pts = None
    gap_home_safe_pts = None
    gap_away_safe_pts = None

    if title_leader_points is None:
        warnings.append("missing title_leader_points")
    else:
        gap_home_title_pts = max(0, int(title_leader_points) - int(hp))
        gap_away_title_pts = max(0, int(title_leader_points) - int(ap))

    if ucl_cutoff_points is None:
        warnings.append("missing ucl_cutoff_points")
    else:
        gap_home_euro_pts = max(0, int(ucl_cutoff_points) - int(hp))
        gap_away_euro_pts = max(0, int(ucl_cutoff_points) - int(ap))

    if relegation_safety_points is None:
        warnings.append("missing relegation_safety_points")
    else:
        gap_home_safe_pts = max(0, int(relegation_safety_points) - int(hp))
        gap_away_safe_pts = max(0, int(relegation_safety_points) - int(ap))

    math_title_home_alive: Optional[bool] = None
    math_title_away_alive: Optional[bool] = None
    math_euro_home_alive: Optional[bool] = None
    math_euro_away_alive: Optional[bool] = None
    math_releg_home_alive: Optional[bool] = None
    math_releg_away_alive: Optional[bool] = None

    matches_remaining: Optional[int] = None
    if season_ctx and season_ctx.rounds_remaining_after_this_match is not None:
        matches_remaining = int(season_ctx.rounds_remaining_after_this_match)
    else:
        if not (season_ctx and season_ctx.total_matchdays):
            warnings.append("missing total_matchdays")
        if standings.home_matches_played is None:
            warnings.append("missing matches_played")
        if season_ctx and season_ctx.total_matchdays and standings.home_matches_played is not None:
            matches_remaining = max(0, int(season_ctx.total_matchdays) - int(standings.home_matches_played))

    if matches_remaining is None:
        warnings.append("missing matches_remaining")
    else:
        if title_leader_points is not None:
            math_title_home_alive = (int(hp) + 3 * matches_remaining) >= int(title_leader_points)
            math_title_away_alive = (int(ap) + 3 * matches_remaining) >= int(title_leader_points)
        if ucl_cutoff_points is not None:
            math_euro_home_alive = (int(hp) + 3 * matches_remaining) >= int(ucl_cutoff_points)
            math_euro_away_alive = (int(ap) + 3 * matches_remaining) >= int(ucl_cutoff_points)
        if relegation_safety_points is not None:
            # Risk alive if still below the safety threshold (points can't decrease).
            math_releg_home_alive = int(hp) < int(relegation_safety_points)
            math_releg_away_alive = int(ap) < int(relegation_safety_points)

    return (
        aux_gap_home_title_pos,
        aux_gap_away_title_pos,
        aux_gap_home_euro_pos,
        aux_gap_away_euro_pos,
        aux_gap_home_releg_pos,
        aux_gap_away_releg_pos,
        gap_home_title_pts,
        gap_away_title_pts,
        gap_home_euro_pts,
        gap_away_euro_pts,
        gap_home_safe_pts,
        gap_away_safe_pts,
        math_title_home_alive,
        math_title_away_alive,
        math_euro_home_alive,
        math_euro_away_alive,
        math_releg_home_alive,
        math_releg_away_alive,
    )


def _classify_target_band(
    side: str,
    standings,
    season_ctx,
    gap_title: Optional[int],
    gap_europe: Optional[int],
    gap_releg: Optional[int],
) -> TargetBand:
    """
    Simplified target band classification:

    - TITLE:
        - position <= 2, OR
        - gap_title is small (<= 2 positions) and not obviously out of race
    - EUROPE:
        - if not TITLE and either:
            - position within [3..7], OR
            - gap_europe small (<= 2)
    - RELEGATION:
        - position close to relegation line (gap_releg <= 2) OR already below
    - MIDTABLE: everything else that has standings
    - UNKNOWN: no standings info

    This is early, league-only heuristic and will be refined later.
    """

    from football_agent.flashscore.models import FlashscoreStandings

    if not isinstance(standings, FlashscoreStandings):
        return "UNKNOWN"

    pos = standings.home_position if side == "home" else standings.away_position
    if pos is None:
        return "UNKNOWN"

    # Title
    if pos <= 2:
        return "TITLE"
    if gap_title is not None and gap_title <= 2:
        return "TITLE"

    # Europe
    if 3 <= pos <= 7:
        return "EUROPE"
    if gap_europe is not None and gap_europe <= 2:
        return "EUROPE"

    # Relegation
    if gap_releg is not None and gap_releg <= 2:
        return "RELEGATION"

    return "MIDTABLE"


def _classify_urgency(
    band: TargetBand,
    rounds_remaining: Optional[int],
    gap_to_target: int,
) -> UrgencyLevel:
    """
    Urgency rules (early version):

    Inputs:
    - band: TITLE/EUROPE/RELEGATION/MIDTABLE/UNKNOWN
    - rounds_remaining: how many rounds after this match (if known)
    - gap_to_target: minimal positive gap in positions to primary target (approx.)

    Rules (high-level intuition):
    - UNKNOWN: if band == UNKNOWN
    - LOW:
        - band == MIDTABLE and rounds_remaining is large (> 10)
    - MEDIUM:
        - default when band has a plausible target and plenty of time (rounds_remaining > 6)
    - HIGH:
        - band in {TITLE, EUROPE, RELEGATION} AND rounds_remaining <= 6
    - CRITICAL:
        - band in {TITLE, EUROPE, RELEGATION} AND rounds_remaining <= 3

    If rounds_remaining is unknown, we default to MEDIUM for non-MIDTABLE.
    """

    if band == "UNKNOWN":
        return "UNKNOWN"

    if rounds_remaining is None:
        if band == "MIDTABLE":
            return "LOW"
        return "MEDIUM"

    if band == "MIDTABLE":
        return "LOW" if rounds_remaining > 10 else "MEDIUM"

    # Teams with explicit targets.
    if rounds_remaining <= 3:
        return "CRITICAL"
    if rounds_remaining <= 6:
        return "HIGH"
    return "MEDIUM"


def _positive_or_inf(value: Optional[int]) -> int:
    if value is None:
        return 9999
    if value < 0:
        return 0
    return value

