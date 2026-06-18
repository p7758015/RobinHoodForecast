"""Tests for eval_pool discovery fallback in accumulate."""

from __future__ import annotations

from football_agent.eval_pool.accumulate import accumulate_league_pool
from football_agent.eval_pool.fixture_sources import FixtureFetchResult, FixtureFetchStats
from football_agent.services.live_flashscore_pipeline import LivePipelineResult
from football_agent.tests.test_competition_discovery import _mock_search


def _fake_pipeline_ok():
    class _P:
        def analyze_flashscore_url(self, match_url: str) -> LivePipelineResult:
            return LivePipelineResult(
                success=True,
                path="flashscore_url",
                persisted=True,
                run_id="r1",
            )

    return _P()


def test_discovery_fallback_used_when_list_empty() -> None:
    discovered_raw = {
        "match_id": "lv-d1",
        "home_team_name": "Riga FC",
        "away_team_name": "Valmiera",
        "source_url": "https://flashscore.com/match/lv-d1",
        "competition_name": "Virsliga",
        "competition_country": "Latvia",
        "date": "2026-06-02",
        "_discovery_source": True,
    }

    def _entry_fetch(entry, date_str, raw_list, *, use_discovery_fallback, resolver=None, fixture_svc=None, **kwargs):
        from_list = [r for r in raw_list if False]
        if use_discovery_fallback and entry.key == "latvia_virsliga":
            return FixtureFetchResult(
                fixtures=[discovered_raw],
                warnings=["discovery_fallback_used:latvia_virsliga"],
                stats=FixtureFetchStats(seen=1, in_range=1),
            )
        return FixtureFetchResult.empty()

    summary = accumulate_league_pool(
        date_from="2026-06-02",
        date_to="2026-06-02",
        league_keys=["latvia_virsliga"],
        fetch_matches_for_date=lambda _d: [],
        pipeline_factory=_fake_pipeline_ok,
        use_discovery_fallback=True,
        fetch_fixtures_for_entry_fn=_entry_fetch,
    )
    assert summary["discovery_fixtures_added"] >= 1
    assert summary["fixtures_in_scope"] == 1
    assert summary["runs"][0]["discovery"] is True


def test_discovery_error_does_not_crash_accumulate() -> None:
    def _entry_fetch(*_a, **_kw):
        raise RuntimeError("discovery down")

    summary = accumulate_league_pool(
        date_from="2026-06-02",
        date_to="2026-06-02",
        league_keys=["latvia_virsliga"],
        fetch_matches_for_date=lambda _d: [],
        pipeline_factory=_fake_pipeline_ok,
        use_discovery_fallback=True,
        fetch_fixtures_for_entry_fn=_entry_fetch,
    )
    assert summary["fixtures_in_scope"] == 0
    assert any("fixture_fetch_error" in w for w in summary["discovery_warnings"])


def test_wave1_without_fallback_unchanged_behavior() -> None:
    kz = {
        "match_id": "kz-1",
        "source_url": "https://flashscore.com/match/?mid=kz-1",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "A",
        "away_team_name": "B",
    }

    class _Route:
        route = "league_full"

    class _P:
        def analyze_flashscore_url(self, match_url: str) -> LivePipelineResult:
            return LivePipelineResult(
                success=True,
                path="flashscore_url",
                persisted=True,
                routing_decision=_Route(),  # type: ignore[arg-type]
            )

    summary = accumulate_league_pool(
        date_from="2026-06-02",
        date_to="2026-06-02",
        league_keys=["kazakhstan_premier"],
        fetch_matches_for_date=lambda _d: [kz],
        pipeline_factory=lambda: _P(),
        use_discovery_fallback=False,
    )
    assert summary["league_full_scored"] == 1
    assert summary["use_discovery_fallback"] is False
