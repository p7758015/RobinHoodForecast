"""Pipeline-level v2 + OpenClaw source selection (mocked ingestion)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from football_agent import app_pipeline
from football_agent.app_pipeline import _create_v2_league_service, handle_request
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.openclaw.models import (
    OpenClawMatchMeta,
    OpenClawMatchPayload,
    OpenClawOddsMarkets,
    OpenClawTeamRef,
)
from football_agent.openclaw.service import OpenClawLeagueAnalysisService
from football_agent.tests.test_openclaw_ingestion import _full_payload


def test_openclaw_service_batch_has_markets() -> None:
    """In-memory OpenClaw batch → scored results with market keys."""

    def _stub_fetch(date_str: str, competition_code: str | None = None):  # noqa: ARG001
        p = OpenClawMatchPayload(
            meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 11, 29, 12, 0, tzinfo=timezone.utc)),
            home_team=OpenClawTeamRef(name="Juventus FC", team_id=1),
            away_team=OpenClawTeamRef(name="AC Milan", team_id=2),
            odds=OpenClawOddsMarkets(home_win=2.1),
        )
        return [p.model_dump()]

    svc = OpenClawLeagueAnalysisService(fetch_matches_fn=_stub_fetch)
    out = svc.analyze_matches_for_date("2025-11-29")
    assert len(out) >= 1
    assert any(m.market_key for m in out[0].market_predictions)


def test_run_v2_uses_openclaw_via_factory(monkeypatch) -> None:
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", True)
    monkeypatch.setattr(app_pipeline, "OPENCLAW_BASE_URL", "http://stub")

    def _fake_factory(fd, af, *, prefer_openclaw=False):  # noqa: ARG001
        return OpenClawLeagueAnalysisService(
            fetch_matches_fn=lambda date_str, cc=None: [_full_payload().model_dump()],  # noqa: ARG005
        )

    monkeypatch.setattr(app_pipeline, "_create_v2_league_service", _fake_factory)
    monkeypatch.setattr(app_pipeline, "format_v2_or_llm", lambda *a, **k: "ok-all")

    with patch.object(app_pipeline, "USE_V2_PIPELINE", True):
        txt = handle_request(
            {"type": "all_matches", "date": "2025-11-29"},
            MagicMock(),
            MagicMock(),
            MagicMock(),
            prefer_openclaw_ingestion=True,
        )
        assert txt == "ok-all"


def test_run_v2_openclaw_single_match(monkeypatch) -> None:
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", True)
    monkeypatch.setattr(app_pipeline, "OPENCLAW_BASE_URL", "http://stub")

    def _fake_factory(fd, af, *, prefer_openclaw=False):  # noqa: ARG001
        pl = OpenClawMatchPayload(
            meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 11, 29, 17, 30, tzinfo=timezone.utc)),
            home_team=OpenClawTeamRef(name="AC Milan", short_name="Milan", team_id=101),
            away_team=OpenClawTeamRef(name="Juventus FC", short_name="Juve", team_id=102),
            odds=OpenClawOddsMarkets(home_win=2.05, draw=3.35, away_win=3.6),
        ).model_dump()
        return OpenClawLeagueAnalysisService(fetch_matches_fn=lambda d, cc=None: [pl])

    monkeypatch.setattr(app_pipeline, "_create_v2_league_service", _fake_factory)
    monkeypatch.setattr(app_pipeline, "format_v2_or_llm", lambda *a, **k: "ok-single")

    with patch.object(app_pipeline, "USE_V2_PIPELINE", True):
        out = handle_request(
            {
                "type": "single_match",
                "date": "2025-11-29",
                "home_team": "Milan",
                "away_team": "Juventus",
            },
            MagicMock(),
            MagicMock(),
            MagicMock(),
            prefer_openclaw_ingestion=True,
        )
        assert out == "ok-single"


def test_run_v2_openclaw_express(monkeypatch) -> None:
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", True)
    monkeypatch.setattr(app_pipeline, "OPENCLAW_BASE_URL", "http://stub")

    def _fake_factory(fd, af, *, prefer_openclaw=False):  # noqa: ARG001
        p1 = _full_payload()
        payload2 = OpenClawMatchPayload(
            meta=OpenClawMatchMeta(match_date_utc=datetime(2025, 11, 29, 14, 0, tzinfo=timezone.utc)),
            home_team=OpenClawTeamRef(name="Roma AS", team_id=201),
            away_team=OpenClawTeamRef(name="SSC Napoli", team_id=202),
            odds=OpenClawOddsMarkets(home_win=2.55, draw=3.25, away_win=2.90),
        )
        return OpenClawLeagueAnalysisService(
            fetch_matches_fn=lambda d, cc=None: [p1.model_dump(), payload2.model_dump()],
        )

    monkeypatch.setattr(app_pipeline, "_create_v2_league_service", _fake_factory)
    monkeypatch.setattr(app_pipeline, "format_v2_or_llm", lambda *a, **k: "ok-ex")

    def _stub_build(self, preds, target_odds):  # noqa: ANN001
        evt = preds[0]
        bm = evt.best_market
        fake = MagicMock()
        fake.events = [MagicMock()]
        fake.events[0].match_meta = evt.match_meta
        fake.events[0].market_key = bm.market_key
        fake.events[0].probability = bm.probability
        fake.events[0].book_odds = bm.book_odds or 1.3
        fake.events[0].label = bm.label or ""
        fake.events[0].edge = bm.edge
        fake.total_odds = target_odds
        fake.total_probability = 0.5
        fake.target_odds = target_odds
        fake.within_tolerance = True
        fake.selection_notes = ""
        return fake

    monkeypatch.setattr(ExpressBuilderV2, "build_express", _stub_build)

    with patch.object(app_pipeline, "USE_V2_PIPELINE", True):
        out = handle_request(
            {"type": "express", "date": "2025-11-29", "target_odds": 3.0},
            MagicMock(),
            MagicMock(),
            MagicMock(),
            prefer_openclaw_ingestion=True,
        )
        assert out == "ok-ex"


def test_factory_falls_back_without_base_url() -> None:
    fd = MagicMock()
    af = MagicMock()
    with patch.object(app_pipeline, "USE_OPENCLAW", True), patch.object(app_pipeline, "OPENCLAW_BASE_URL", ""):
        svc = _create_v2_league_service(fd, af, prefer_openclaw=True)
        assert type(svc).__name__ == "LeagueAnalysisServiceV2"


def test_create_v2_service_logs_openclaw_when_enabled(caplog, monkeypatch) -> None:
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", True)
    monkeypatch.setattr(app_pipeline, "OPENCLAW_BASE_URL", "http://stub")
    _create_v2_league_service(MagicMock(), MagicMock(), prefer_openclaw=True)
    assert any("OpenClaw ingestion" in r.message for r in caplog.records)


def test_create_v2_service_logs_legacy(caplog, monkeypatch) -> None:
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", False)
    fd, af = MagicMock(), MagicMock()
    _create_v2_league_service(fd, af, prefer_openclaw=False)
    assert any("legacy API ingestion" in r.message for r in caplog.records)


def test_prefer_openclaw_false_keeps_legacy_even_if_env_would_allow(caplog, monkeypatch) -> None:
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(app_pipeline, "USE_OPENCLAW", True)
    monkeypatch.setattr(app_pipeline, "OPENCLAW_BASE_URL", "http://stub")
    _create_v2_league_service(MagicMock(), MagicMock(), prefer_openclaw=False)
    assert any("legacy API ingestion" in r.message for r in caplog.records)
    assert not any("OpenClaw ingestion" in r.message for r in caplog.records)
