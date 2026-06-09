from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from football_agent.domain.models import CoachMatch, H2HStats, MatchResult, StandingEntry
from football_agent.league_registry import UNKNOWN_TOTAL_ROUNDS_WARNING, resolve_league_params


def calculate_motivation(
    position: int,
    points: int,
    played_rounds: int,
    total_rounds: Optional[int],
    standings: List[StandingEntry],
    competition_code: str,
) -> Tuple[float, bool, bool, List[str]]:
    """Returns (motivation, eliminated, is_fighting, derivation_warnings)."""

    warnings: List[str] = []
    params = resolve_league_params(competition_code)
    rel_slots = params.relegation_slots
    euro_cfg = params.euro_slots
    euro_slots = euro_cfg["ucl"] + euro_cfg["uel"]

    total_teams = len(standings)

    def pts_at(pos: int) -> int:
        entry = next((s for s in standings if s.position == pos), None)
        return entry.points if entry else 0

    if total_rounds is None:
        warnings.append(UNKNOWN_TOTAL_ROUNDS_WARNING)
        relz_border = total_teams - rel_slots + 1
        border_pts = pts_at(relz_border)
        survival_motivation = 0.0
        if position >= relz_border:
            survival_motivation = 1.0
        elif (relz_border - 3) <= position < relz_border:
            diff = points - border_pts
            survival_motivation = max(0.5, min(0.9, 1.0 - diff / 3.0))

        euro_border_pts = pts_at(euro_slots)
        euro_motivation = 0.0
        if position <= euro_slots:
            pts_to_drop = points - euro_border_pts
            euro_motivation = max(0.5, min(1.0, 0.5 + (3 - pts_to_drop) * 0.1))
        elif position <= euro_slots + 5:
            diff = euro_border_pts - points
            euro_motivation = max(0.4, min(0.9, 1.0 - diff / 5.0))

        raw = max(survival_motivation, euro_motivation)
        is_fighting = raw > 0.3
        if raw == 0.0:
            motivation = 0.2 if (5 < position < total_teams - 4) else 0.3
        else:
            motivation = raw
        return motivation, False, is_fighting, warnings

    games_remaining = total_rounds - played_rounds
    max_pts = points + 3 * games_remaining

    # --- Зона вылета ---
    relz_border = total_teams - rel_slots + 1
    border_pts = pts_at(relz_border)
    threshold_safe = border_pts + 1

    eliminated = max_pts < threshold_safe
    survival_motivation = 0.0
    if not eliminated:
        if position >= relz_border:
            survival_motivation = 1.0
        elif (relz_border - 3) <= position < relz_border:
            diff = points - border_pts
            survival_motivation = max(0.5, min(0.9, 1.0 - diff / 3.0))

    # --- Еврокубки ---
    euro_border_pts = pts_at(euro_slots)
    euro_motivation = 0.0
    if position <= euro_slots:
        pts_to_drop = points - euro_border_pts
        euro_motivation = max(0.5, min(1.0, 0.5 + (3 - pts_to_drop) * 0.1))
    elif position <= euro_slots + 5:
        diff = euro_border_pts - points
        if max_pts >= euro_border_pts + 1:
            euro_motivation = max(0.4, min(0.9, 1.0 - diff / 5.0))

    raw = max(survival_motivation, euro_motivation)
    is_fighting = raw > 0.3

    if raw == 0.0:
        motivation = 0.2 if (5 < position < total_teams - 4) else 0.3
    else:
        motivation = raw

    return motivation, eliminated, is_fighting, warnings


def calculate_coach_strength(
    coach_matches: List[CoachMatch],
    opponent_team_id: int,
) -> float:
    """
    Возвращает сырое значение.
    Вызывать для обоих тренеров, затем нормировать через normalize_coach_pair.
    """

    total = len(coach_matches)
    if total == 0:
        return 0.5

    wins = sum(1 for m in coach_matches if m.result == "W")
    base_wr = wins / total
    alpha = min(total / 20.0, 1.0)
    global_strength = 0.5 * (1 - alpha) + base_wr * alpha

    h2h = [m for m in coach_matches if m.opponent_id == opponent_team_id]
    if not h2h:
        h2h_wr = 0.5
    else:
        h2h_wr = sum(1 for m in h2h if m.result == "W") / len(h2h)

    beta = min(len(h2h) / 10.0, 1.0)
    return global_strength * (1 - beta) + h2h_wr * beta


def normalize_coach_pair(c_home: float, c_away: float) -> Tuple[float, float]:
    s = c_home + c_away
    if s == 0:
        return 0.5, 0.5
    return c_home / s, c_away / s


def calculate_form(
    season_matches: List[MatchResult],
    coach_start_date: Optional[date],
    is_home: bool,
) -> float:
    """Возвращает F ∈ [0, 1]."""

    def pts(ms: List[MatchResult]) -> int:
        return sum(3 if m.result == "W" else 1 if m.result == "D" else 0 for m in ms)

    def clip(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    finished = [m for m in season_matches if m.result in ("W", "D", "L")]
    if not finished:
        return 0.5

    total = len(finished)
    season_pts_rate = pts(finished) / (3 * total)
    gd = sum(m.goals_for - m.goals_against for m in finished)
    gd_score = clip(0.5 + 0.5 * gd / (2 * total), 0.0, 1.0)

    home_m = [m for m in finished if m.is_home]
    away_m = [m for m in finished if not m.is_home]
    home_rate = pts(home_m) / (3 * len(home_m)) if home_m else 0.5
    away_rate = pts(away_m) / (3 * len(away_m)) if away_m else 0.5

    # Форма при текущем тренере
    if coach_start_date:
        coach_m = [m for m in finished if m.date >= coach_start_date]
    else:
        coach_m = finished
    gamma = min(len(coach_m) / 10.0, 1.0)
    coach_rate = pts(coach_m) / (3 * len(coach_m)) if coach_m else season_pts_rate
    form_with_coach = season_pts_rate * (1 - gamma) + coach_rate * gamma

    # Последние 5 матчей
    recent = sorted(finished, key=lambda m: m.date, reverse=True)[:5]
    rn = len(recent)
    r_pts = pts(recent) / (3 * rn)
    r_gd = sum(m.goals_for - m.goals_against for m in recent)
    r_gd_score = clip(0.5 + 0.5 * r_gd / (2 * rn), 0.0, 1.0)
    recent_form = 0.6 * r_pts + 0.4 * r_gd_score

    form_combined = form_with_coach * 0.3 + recent_form * 0.7
    venue_rate = home_rate if is_home else away_rate
    return clip(0.7 * form_combined + 0.3 * venue_rate, 0.0, 1.0)


def calculate_h2h_stats(
    h2h_matches: List[MatchResult],
    current_home_team_id: int,
) -> H2HStats:
    """
    h2h_matches — исторические матчи между двумя командами,
    с точки зрения команды-хозяина текущего матча.
    Берём все доступные данные без ограничения по сезону.
    """

    if not h2h_matches:
        return H2HStats(
            total_matches=0,
            home_wins=0,
            away_wins=0,
            draws=0,
            home_goals_avg=0.0,
            away_goals_avg=0.0,
            btts_rate=0.5,
            over25_rate=0.5,
        )

    total = len(h2h_matches)
    home_wins = sum(1 for m in h2h_matches if m.result == "W")
    draws = sum(1 for m in h2h_matches if m.result == "D")
    away_wins = total - home_wins - draws

    home_goals_avg = sum(m.goals_for for m in h2h_matches) / total
    away_goals_avg = sum(m.goals_against for m in h2h_matches) / total

    btts = sum(1 for m in h2h_matches if m.goals_for >= 1 and m.goals_against >= 1)
    over25 = sum(1 for m in h2h_matches if m.goals_for + m.goals_against >= 3)

    return H2HStats(
        total_matches=total,
        home_wins=home_wins,
        away_wins=away_wins,
        draws=draws,
        home_goals_avg=round(home_goals_avg, 2),
        away_goals_avg=round(away_goals_avg, 2),
        btts_rate=round(btts / total, 3),
        over25_rate=round(over25 / total, 3),
    )
