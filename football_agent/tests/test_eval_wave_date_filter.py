"""Tests for eval wave date filtering and cleanup."""

from __future__ import annotations

import tempfile
from pathlib import Path

from football_agent.eval_pool.accumulate import accumulate_league_pool
from football_agent.eval_pool.fixture_date import (
    evaluate_fixture_date_guard,
    extract_fixture_date,
    filter_fixtures_in_date_range,
    fixture_in_date_range,
    is_discovery_fixture,
)
from football_agent.eval_pool.fixture_sources import FixtureFetchResult, FixtureFetchStats, fetch_fixtures_for_pool_entry
from football_agent.eval_pool.scope import WAVE1_LEAGUE_POOL
from football_agent.eval_pool.wave_cleanup import cleanup_wave_runs, collect_wave_runs
from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_from_fixtures
from football_agent.services.live_flashscore_pipeline import LivePipelineResult

FIXTURES = Path(__file__).parent / "data"


def test_fixture_out_of_range_rejected() -> None:
    raw = {
        "match_id": "x1",
        "kickoff_utc": "2026-06-26T15:00:00+00:00",
        "home_team_name": "A",
        "away_team_name": "B",
    }
    assert fixture_in_date_range(raw, "2026-06-18", "2026-06-21") is False
    kept, skipped = filter_fixtures_in_date_range([raw], "2026-06-18", "2026-06-21")
    assert kept == []
    assert skipped == 1


def test_discovery_fallback_filters_out_of_range() -> None:
    entry = next(e for e in WAVE1_LEAGUE_POOL if e.key == "latvia_virsliga")

    class _FakeDiscovered:
        fixtures = []
        warnings = []

    class _FakeFixture:
        def __init__(self, date: str) -> None:
            self.match_id = "m1"
            self.match_url = "https://flashscore.com/match/m1"
            self.home_team = "H"
            self.away_team = "A"
            self.kickoff_utc = f"{date}T12:00:00+00:00"
            self.match_date = date
            self.status = "scheduled"
            self.competition_name = "Virsliga"
            self.competition_country = "Latvia"
            self.raw = {}

    class _FakeFixtureSvc:
        def list_competition_fixtures(self, *_a, **_kw):
            _FakeDiscovered.fixtures = [
                _FakeFixture("2026-06-20"),
                _FakeFixture("2026-07-05"),
            ]
            return _FakeDiscovered()

    class _FakeResolver:
        def resolve_competition(self, _q):
            from football_agent.discovery.models import CompetitionCandidate, CompetitionResolveResult, ResolvedCompetition

            c = CompetitionCandidate("Virsliga", "Latvia", "http://x", source="test")
            return CompetitionResolveResult(
                query="x",
                candidates=[c],
                resolved=ResolvedCompetition(candidate=c),
                ambiguous=False,
                warnings=[],
            )

    result = fetch_fixtures_for_pool_entry(
        entry,
        "2026-06-20",
        [],
        use_discovery_fallback=True,
        wave_date_from="2026-06-18",
        wave_date_to="2026-06-21",
        resolver=_FakeResolver(),
        fixture_svc=_FakeFixtureSvc(),
    )
    assert len(result.fixtures) == 1
    assert result.fixtures[0]["date"] == "2026-06-20"
    assert result.stats.skipped_out_of_range == 1
    assert any("discovery_out_of_range_skipped" in w for w in result.warnings)


def test_unresolved_competition_returns_fixture_fetch_result() -> None:
    entry = next(e for e in WAVE1_LEAGUE_POOL if e.key == "kazakhstan_premier")

    class _UnresolvedResolver:
        def resolve_competition(self, _q):
            from football_agent.discovery.models import CompetitionResolveResult

            return CompetitionResolveResult(
                query="x",
                candidates=[],
                resolved=None,
                ambiguous=False,
                warnings=["not_found"],
            )

    result = fetch_fixtures_for_pool_entry(
        entry,
        "2026-06-18",
        [],
        use_discovery_fallback=True,
        resolver=_UnresolvedResolver(),
    )
    assert isinstance(result, FixtureFetchResult)
    assert result.fixtures == []
    assert result.stats.seen == 0


def test_accumulate_summary_out_of_range_skipped() -> None:
    in_range = {
        "match_id": "kz-1",
        "source_url": "https://flashscore.com/match/?mid=kz-1",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "A",
        "away_team_name": "B",
        "kickoff_utc": "2026-06-20T12:00:00+00:00",
    }
    out_range = {
        **in_range,
        "match_id": "kz-2",
        "source_url": "https://flashscore.com/match/?mid=kz-2",
        "kickoff_utc": "2026-06-26T12:00:00+00:00",
    }

    class _Route:
        route = "league_full"

    class _P:
        def analyze_flashscore_url(self, match_url: str, **kwargs) -> LivePipelineResult:
            return LivePipelineResult(
                success=True,
                path="flashscore_url",
                persisted=True,
                routing_decision=_Route(),  # type: ignore[arg-type]
            )

    def _entry_fetch(entry, date_str, raw_list, *, use_discovery_fallback, **kwargs):
        from football_agent.eval_pool.fixture_date import filter_fixtures_in_date_range

        matched = [r for r in raw_list if r.get("competition_country") == "Kazakhstan"]
        filtered, skipped = filter_fixtures_in_date_range(
            matched, "2026-06-18", "2026-06-21", loop_date=date_str,
        )
        return FixtureFetchResult.from_parts(
            filtered, [], seen=len(matched), in_range=len(filtered), skipped_out_of_range=skipped,
        )

    summary = accumulate_league_pool(
        date_from="2026-06-18",
        date_to="2026-06-21",
        league_keys=["kazakhstan_premier"],
        expected_matches=2,
        fetch_matches_for_date=lambda _d: [in_range, out_range],
        pipeline_factory=lambda: _P(),
        use_discovery_fallback=False,
        fetch_fixtures_for_entry_fn=_entry_fetch,
    )
    assert summary["fixtures_out_of_range_skipped"] >= 1
    assert summary["fixtures_in_range"] == 1
    assert summary["league_full_scored"] == 1
    assert any("expected_matches_mismatch" in w for w in summary["discovery_warnings"])


def test_cleanup_wave_dry_run_and_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "cleanup.db"
    run_v2_batch_persist_from_fixtures(
        FIXTURES,
        [{"flashscore_stem": "flashscore_kazakhstan_premier_match", "home_score": 1, "away_score": 0}],
        db_path=db_path,
        save_match_results=True,
    )
    manifest = EvalWaveManifest(
        wave_name="test_wave",
        label="test",
        date_from="2026-01-01",
        date_to="2026-12-31",
        league_keys=("kazakhstan_premier",),
    )
    refs = collect_wave_runs(manifest, db_path=db_path)
    assert len(refs) == 1

    preview = cleanup_wave_runs(manifest, db_path=db_path, dry_run=True)
    assert preview["runs_matched"] == 1
    assert preview["runs_deleted"] == 0

    applied = cleanup_wave_runs(manifest, db_path=db_path, dry_run=False)
    assert applied["runs_deleted"] == 1
    assert len(collect_wave_runs(manifest, db_path=db_path)) == 0


def test_undated_discovery_fixture_rejects_loop_day_fallback() -> None:
    raw = {
        "_discovery_source": True,
        "match_id": "est-july",
        "home_team_name": "Nomme Utd",
        "away_team_name": "Narva",
        "source_url": "https://flashscore.com/match/?mid=est-july",
        "_discovery_date_from": "2026-06-18",
    }
    assert is_discovery_fixture(raw)
    assert fixture_in_date_range(raw, "2026-06-18", "2026-06-21", loop_date="2026-06-18") is False


def test_flashscore_display_time_in_wave_range() -> None:
    raw = {
        "_discovery_source": True,
        "_discovery_date_from": "2026-06-18",
        "match_id": "est-20",
        "home_team_name": "Levadia",
        "away_team_name": "Kalju",
        "time": "20.06. 17:00",
    }
    assert extract_fixture_date(raw) == "2026-06-20"
    kept, skipped = filter_fixtures_in_date_range(
        [raw], "2026-06-18", "2026-06-21", allow_loop_date_fallback=False,
    )
    assert len(kept) == 1 and skipped == 0


def test_flashscore_display_time_out_of_wave_range() -> None:
    raw = {
        "_discovery_source": True,
        "_discovery_date_from": "2026-06-18",
        "time": "26.06. 20:00",
    }
    assert extract_fixture_date(raw) == "2026-06-26"
    assert fixture_in_date_range(raw, "2026-06-18", "2026-06-21") is False


def test_july_31_meistriliiga_fixture_rejected_for_june_wave() -> None:
    raw = {
        "_discovery_source": True,
        "match_id": "est-july31",
        "home_team_name": "Nomme Utd",
        "away_team_name": "Narva",
        "kickoff_utc": "2026-07-31T17:00:00+00:00",
        "date": "2026-07-31",
        "source_url": "https://flashscore.com/match/?mid=est-july31",
        "competition_name": "Meistriliiga",
        "competition_country": "Estonia",
    }
    assert fixture_in_date_range(raw, "2026-06-18", "2026-06-21") is False
    kept, skipped = filter_fixtures_in_date_range(
        [raw], "2026-06-18", "2026-06-21", allow_loop_date_fallback=False,
    )
    assert kept == []
    assert skipped == 1


def test_accumulate_blocks_undated_discovery_july_fixture() -> None:
    """Undated discovery fixture must not reach pipeline/persist (July leak regression)."""
    undated_july = {
        "_discovery_source": True,
        "match_id": "est-undated",
        "home_team_name": "Nomme Utd",
        "away_team_name": "Narva",
        "source_url": "https://flashscore.com/match/?mid=est-undated",
        "competition_name": "Meistriliiga",
        "competition_country": "Estonia",
    }
    july_dated = {
        **undated_july,
        "match_id": "est-july31",
        "time": "31.07. 17:00",
        "source_url": "https://flashscore.com/match/?mid=est-july31",
        "_discovery_date_from": "2026-06-18",
    }
    in_wave = {
        "_discovery_source": True,
        "match_id": "est-in",
        "home_team_name": "Flora",
        "away_team_name": "Paide",
        "time": "20.06. 17:00",
        "source_url": "https://flashscore.com/match/?mid=est-in",
        "competition_name": "Meistriliiga",
        "competition_country": "Estonia",
        "_discovery_date_from": "2026-06-18",
    }
    pipeline_calls: list[str] = []

    class _Route:
        route = "league_full"

    class _P:
        def analyze_flashscore_url(self, match_url: str, **kwargs) -> LivePipelineResult:
            pipeline_calls.append(match_url)
            return LivePipelineResult(
                success=True,
                path="flashscore_url",
                persisted=True,
                routing_decision=_Route(),  # type: ignore[arg-type]
            )

    def _entry_fetch(entry, date_str, raw_list, *, use_discovery_fallback, **kwargs):
        from football_agent.eval_pool.fixture_date import filter_fixtures_in_date_range

        fixtures = [undated_july, july_dated, in_wave]
        filtered, skipped = filter_fixtures_in_date_range(
            fixtures,
            "2026-06-18",
            "2026-06-21",
            loop_date=date_str,
            allow_loop_date_fallback=False,
        )
        return FixtureFetchResult.from_parts(
            filtered, [], seen=len(fixtures), in_range=len(filtered), skipped_out_of_range=skipped,
        )

    summary = accumulate_league_pool(
        date_from="2026-06-18",
        date_to="2026-06-21",
        league_keys=["estonia_meistriliiga"],
        fetch_matches_for_date=lambda _d: [],
        pipeline_factory=lambda: _P(),
        use_discovery_fallback=True,
        fetch_fixtures_for_entry_fn=_entry_fetch,
    )
    assert summary["fixtures_out_of_range_skipped"] >= 2
    assert summary["fixtures_in_range"] == 1
    assert summary["league_full_scored"] == 1
    assert len(pipeline_calls) == 1
    assert "est-in" in pipeline_calls[0]


def test_in_range_fixture_still_works() -> None:
    raw = {
        "match_id": "kz-ok",
        "kickoff_utc": "2026-06-19T18:00:00+00:00",
        "competition_name": "Premier League",
        "competition_country": "Kazakhstan",
        "home_team_name": "A",
        "away_team_name": "B",
        "source_url": "https://flashscore.com/match/?mid=kz-ok",
    }

    class _Route:
        route = "league_full"

    class _P:
        def analyze_flashscore_url(self, _u: str, **kwargs) -> LivePipelineResult:
            return LivePipelineResult(
                success=True,
                path="flashscore_url",
                persisted=True,
                routing_decision=_Route(),  # type: ignore[arg-type]
            )

    summary = accumulate_league_pool(
        date_from="2026-06-18",
        date_to="2026-06-21",
        league_keys=["kazakhstan_premier"],
        fetch_matches_for_date=lambda _d: [raw],
        pipeline_factory=lambda: _P(),
        use_discovery_fallback=False,
    )
    assert summary["fixtures_in_range"] == 1
    assert summary["fixtures_out_of_range_skipped"] == 0
