"""Smoke/debug utilities for OpenClaw: completeness summary and warnings."""

from __future__ import annotations

from datetime import datetime, timezone

from football_agent.domain.enums_v2 import TournamentType
from football_agent.debug.openclaw_trace import (
    build_trace_report,
    snapshot_completeness_summary,
)
from football_agent.openclaw.adapter import OpenClawSnapshotBuilder
from football_agent.openclaw.models import (
    OpenClawMatchMeta,
    OpenClawMatchPayload,
    OpenClawOddsMarkets,
    OpenClawTeamRef,
)
from football_agent.tests.test_openclaw_ingestion import _full_payload


def test_trace_report_does_not_crash_on_partial_payload() -> None:
    p = OpenClawMatchPayload(
        meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 1, 1, 12, tzinfo=timezone.utc)),
        home_team=OpenClawTeamRef(name="Team A"),
        away_team=OpenClawTeamRef(name="Team B"),
        odds=OpenClawOddsMarkets(home_win=2.1),
    )
    r = build_trace_report(p)
    assert r["using_openclaw_path"] is True
    assert "snapshot_summary" in r
    assert "score_summary" in r
    assert r["score_summary"]["best_market"] is not None


def test_completeness_summary_marks_odds_and_blocks() -> None:
    snap = OpenClawSnapshotBuilder().build(_full_payload())
    s = snapshot_completeness_summary(snap)
    assert s["match"]["competition"] == "SA"
    assert "odds" in s["blocks"]
    assert isinstance(s["blocks"]["odds"], str) and "markets" in s["blocks"]["odds"]


def test_non_league_warning_when_cup() -> None:
    p = _full_payload()
    p.meta.tournament_type = TournamentType.DOMESTIC_CUP
    r = build_trace_report(p)
    assert r["non_league_warning"] is True

