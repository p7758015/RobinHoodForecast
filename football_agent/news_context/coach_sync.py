"""Sync nested coach blocks with legacy flat CoachContextBlock fields."""

from __future__ import annotations

from football_agent.news_context.models import CoachContextBlock, CoachNewsContextBlock, CoachStatContextBlock


def sync_coach_context_block(coach: CoachContextBlock) -> CoachContextBlock:
    """Mirror ``news`` + ``stat`` into flat legacy fields for downstream merge/scorer."""
    n: CoachNewsContextBlock = coach.news
    s: CoachStatContextBlock = coach.stat
    return coach.model_copy(
        update={
            "home_coach_name": n.home_coach_name,
            "away_coach_name": n.away_coach_name,
            "home_coach_status": n.home_coach_status,
            "away_coach_status": n.away_coach_status,
            "home_coach_recent_quotes": list(n.home_coach_recent_quotes or []),
            "away_coach_recent_quotes": list(n.away_coach_recent_quotes or []),
            "home_coach_rotation_signal": n.home_coach_rotation_signal,
            "away_coach_rotation_signal": n.away_coach_rotation_signal,
            "home_coach_morale_signal": n.home_coach_morale_signal,
            "away_coach_morale_signal": n.away_coach_morale_signal,
            "home_coach_tactical_signal": n.home_coach_tactical_signal,
            "away_coach_tactical_signal": n.away_coach_tactical_signal,
            "home_coach_absence_signal": n.home_coach_absence_signal,
            "away_coach_absence_signal": n.away_coach_absence_signal,
            "coach_fixture_congestion_comment": n.coach_fixture_congestion_comment,
            "coach_priority_signal": n.coach_priority_signal,
            "coach_news_confidence": n.coach_news_confidence,
            "coach_news_freshness": n.coach_news_freshness,
            "coach_context_sources": list(n.coach_context_sources or []),
            "coach_context_generated_at_utc": n.coach_context_generated_at_utc,
            "home_coach_tenure_days": s.home_coach_tenure_days,
            "away_coach_tenure_days": s.away_coach_tenure_days,
            "coach_h2h_total_matches": s.coach_h2h_total_matches,
            "coach_h2h_home_wins": s.coach_h2h_home_wins,
            "coach_h2h_away_wins": s.coach_h2h_away_wins,
            "coach_h2h_draws": s.coach_h2h_draws,
            "coach_h2h_goal_diff": s.coach_h2h_goal_diff,
            "coach_h2h_last_meeting_date": s.coach_h2h_last_meeting_date,
            "coach_h2h_recent_summary": s.coach_h2h_recent_summary,
            "coach_h2h_confidence": s.coach_h2h_confidence,
            "home_coach_vs_away_team_matches": s.home_coach_vs_away_team_matches,
            "home_coach_vs_away_team_wins": s.home_coach_vs_away_team_wins,
            "away_coach_vs_home_team_matches": s.away_coach_vs_home_team_matches,
            "away_coach_vs_home_team_wins": s.away_coach_vs_home_team_wins,
            "missing_fields": list(dict.fromkeys([*(n.missing_fields or []), *(s.missing_fields or [])])),
            "warnings": list(dict.fromkeys([*(coach.warnings or []), *(n.warnings or []), *(s.warnings or [])])),
        },
    )
