"""Live pipeline OpenClaw enrichment tests (mock/fixture, no network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.output.match_context_display import extract_openclaw_highlights, format_data_sources_line
from football_agent.output.telegram_match_output import format_telegram_match_reply
from football_agent.flashscore.service import FlashscoreIngestionService as FSSvc
from football_agent.services.enrichment_config import EnrichmentRouting, resolve_enrichment_routing
from football_agent.services.enrichment_contract import (
    ENRICHMENT_MODE_NOT_CONFIGURED,
    ENRICHMENT_MODE_SPLIT,
    ODDS_SOURCE_NONE,
    ODDS_SOURCE_OPENCLAW,
    SOURCE_SKIPPED_NOT_CONFIGURED,
)
from football_agent.services.enrichment_live import EnrichmentFetchResult
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline

FIXTURES = Path(__file__).parent / "data"


def _botola_facts() -> FlashscoreMatchFacts:
    facts = FlashscoreIngestionService(
        FixtureFileFlashscoreAdapter(FIXTURES),
    ).get_facts_for_match("flashscore_botola_sample_match")
    assert facts is not None
    return facts


def _openclaw_context():
    ctx = OpenClawContextIngestionService(
        FixtureFileOpenClawContextAdapter(FIXTURES),
    ).get_context_for_fixture("openclaw_context_sample")
    assert ctx is not None
    return ctx


def _botola_odds():
    ctx = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES)).get_odds_for_fixture(
        "odds_botola_sample_match",
    )
    assert ctx is not None
    return ctx


def _enrichment_result(
    *,
    context=None,
    odds=None,
    openclaw: str = "ok",
    odds_status: str = "skipped",
    warnings=None,
    odds_source: str = ODDS_SOURCE_NONE,
    enrichment_backend: str = "openclaw",
    routing: EnrichmentRouting | None = None,
) -> EnrichmentFetchResult:
    if routing is None:
        routing = EnrichmentRouting(
            openclaw_base_url="http://oc" if enrichment_backend != "none" else None,
            context_base_url="http://oc" if enrichment_backend != "none" else None,
            odds_base_url="http://oc" if odds_source == ODDS_SOURCE_OPENCLAW else None,
            enrichment_mode=ENRICHMENT_MODE_NOT_CONFIGURED if enrichment_backend == "none" else ENRICHMENT_MODE_SPLIT,
            odds_source=odds_source,
            odds_separate_service=odds_source == "separate_service",
            openclaw_provides_odds=odds_source == ODDS_SOURCE_OPENCLAW,
            configured=enrichment_backend != "none",
        )
    return EnrichmentFetchResult(
        context=context,
        odds=odds,
        sources={
            "openclaw": openclaw,
            "odds": odds_status,
            "enrichment_backend": enrichment_backend,
        },
        warnings=warnings or [],
        routing=routing,
    )


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_with_openclaw_success(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    oc = _openclaw_context()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(context=oc, openclaw="ok", odds_status="skipped")

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources["flashscore"] == "ok"
    assert result.sources["openclaw"] == "ok"
    assert result.context_highlights
    assert result.openclaw_link_strategy is not None
    assert result.scored_run is not None


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_with_odds_and_openclaw(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(
        context=_openclaw_context(),
        odds=_botola_odds(),
        openclaw="ok",
        odds_status="ok",
        odds_source="openclaw",
    )

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources["odds"] == "ok"
    assert result.sources["openclaw"] == "ok"
    assert result.odds_link_strategy is not None
    assert result.scored_run is not None
    assert result.scored_run.prediction.best_market is not None


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_odds_unavailable_continues(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(
        openclaw="skipped",
        odds_status="failed",
        warnings=["odds_fetch_failed:timeout"],
    )

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources["odds"] == "failed"
    assert "odds_unavailable" in result.warnings
    assert result.scored_run is not None


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_flashscore_only_when_openclaw_unavailable(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(
        openclaw="failed",
        odds_status="failed",
        warnings=["openclaw_context_fetch_failed:timeout"],
    )

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources["openclaw"] == "failed"
    assert "openclaw_context_unavailable" in result.warnings
    assert result.context_highlights == []
    assert result.scored_run is not None


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_openclaw_skipped(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(openclaw="skipped", odds_status="skipped")

    result = LiveFlashscorePipeline(skip_openclaw=True, persist=False).analyze_flashscore_url(
        "https://example.com/m",
    )

    assert result.success is True
    assert result.sources["openclaw"] == "skipped"
    assert "openclaw_context_skipped" in result.warnings


def test_extract_openclaw_highlights_from_fixture() -> None:
    highlights = extract_openclaw_highlights(_openclaw_context())
    assert highlights
    assert any("отсутствует" in h or "Новости" in h for h in highlights)


def test_formatter_shows_sources_and_openclaw_highlights() -> None:
    from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
    from football_agent.services.live_flashscore_pipeline import LivePipelineResult
    from football_agent.services.scoring_service_v2 import ScoredRunV2
    from football_agent.tests.test_scorer_v2 import make_snapshot
    from unittest.mock import MagicMock

    snap = make_snapshot(with_odds=True)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = ["odds_context"]
    report.merge_warnings = []
    report.odds_link_strategy = "none"
    report.openclaw_link_strategy = "by_teams_and_date"

    result = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=ScoredRunV2(snapshot=snap, prediction=pred, build_report=report),
        sources={"flashscore": "ok", "openclaw": "ok"},
        context_highlights=["Хозяева: отсутствует Defender X (muscle injury)"],
        openclaw_link_strategy="by_teams_and_date",
    )

    text = format_telegram_match_reply(result)
    assert "📡 Источники" in text
    assert "OpenClaw" in text
    assert "Defender X" in text
    assert "Лучший рынок" in text


def test_format_data_sources_line_variants() -> None:
    assert "Flashscore + OpenClaw" in format_data_sources_line(
        {"flashscore": "ok", "openclaw": "ok"},
    )
    assert "недоступен" in format_data_sources_line(
        {"flashscore": "ok", "openclaw": "failed"},
    )
    assert "OpenClaw (контекст + линия)" in format_data_sources_line(
        {"flashscore": "ok", "openclaw": "ok", "odds": "ok"},
        odds_source="openclaw",
        enrichment_backend="openclaw",
    )
    assert "не подключён" in format_data_sources_line(
        {"flashscore": "ok", "openclaw": SOURCE_SKIPPED_NOT_CONFIGURED},
    )


def test_formatter_with_bookmaker_odds() -> None:
    from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
    from football_agent.services.live_flashscore_pipeline import LivePipelineResult
    from football_agent.services.scoring_service_v2 import ScoredRunV2
    from football_agent.tests.test_scorer_v2 import make_snapshot
    from unittest.mock import MagicMock

    snap = make_snapshot(with_odds=True)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = []
    report.merge_warnings = []
    report.odds_link_strategy = "by_match_id"
    report.openclaw_link_strategy = "by_teams_and_date"

    result = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=ScoredRunV2(snapshot=snap, prediction=pred, build_report=report),
        sources={"flashscore": "ok", "openclaw": "ok", "odds": "ok"},
        odds_link_strategy="by_match_id",
    )

    text = format_telegram_match_reply(result)
    assert "линия" in text
    assert "кф" in text
    assert "без линии букмекера" not in text.lower()


def test_formatter_without_odds_warns() -> None:
    from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
    from football_agent.services.live_flashscore_pipeline import LivePipelineResult
    from football_agent.services.scoring_service_v2 import ScoredRunV2
    from football_agent.tests.test_scorer_v2 import make_snapshot
    from unittest.mock import MagicMock

    snap = make_snapshot(with_odds=False)
    pred = LeagueScorerV2().score_snapshot(snap)
    report = MagicMock()
    report.merge_missing_blocks = ["odds_context"]
    report.merge_warnings = []
    report.odds_link_strategy = "unlinked"
    report.openclaw_link_strategy = "unlinked"

    result = LivePipelineResult(
        success=True,
        path="flashscore_url",
        scored_run=ScoredRunV2(snapshot=snap, prediction=pred, build_report=report),
        sources={"flashscore": "ok", "odds": "failed"},
        odds_link_strategy="unlinked",
    )

    text = format_telegram_match_reply(result)
    assert "⚠️" in text
    assert "линия" in text.lower()


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch(
    "football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts",
    return_value=_enrichment_result(
        openclaw=SOURCE_SKIPPED_NOT_CONFIGURED,
        odds_status=SOURCE_SKIPPED_NOT_CONFIGURED,
        enrichment_backend="none",
        odds_source=ODDS_SOURCE_NONE,
    ),
)
def test_pipeline_rich_flashscore_enriched_raw(_mock_enrich, mock_fs, _mock_url) -> None:
    svc = FSSvc.__new__(FSSvc)
    facts = svc._map_raw_to_facts(  # type: ignore[attr-defined]
        {
            "match_id": "rich1",
            "home_team_name": "Home FC",
            "away_team_name": "Away FC",
            "competition_name": "Test League",
            "home_form": ["W", "W", "D"],
            "away_form": ["L", "W"],
            "home_position": 2,
            "away_position": 9,
            "h2h": [{"score": "2-0"}, {"score": "1-1"}],
            "round": "12",
        },
    )
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.completeness is not None
    assert result.completeness.flashscore_blocks.get("form") is True
    assert result.completeness.flashscore_blocks.get("h2h") is True
    assert result.scored_run is not None


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch("football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts")
def test_pipeline_bad_payload_odds_fallback(mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)
    mock_enrich.return_value = _enrichment_result(
        openclaw="skipped",
        odds_status="failed",
        warnings=["odds_fetch_failed:bad_payload", "odds_detail: Invalid JSON"],
    )

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")

    assert result.success is True
    assert result.sources["odds"] == "failed"
    assert any("bad_payload" in w for w in result.warnings)
    assert result.completeness is not None
    assert result.completeness.odds_status == "failed"


@patch("football_agent.services.live_flashscore_pipeline._resolve_scraper_url", return_value="http://localhost:3000")
@patch("football_agent.services.live_flashscore_pipeline.LiveFlashscorePipeline._fetch_facts")
@patch(
    "football_agent.services.live_flashscore_pipeline.fetch_enrichment_for_facts",
    return_value=_enrichment_result(
        openclaw=SOURCE_SKIPPED_NOT_CONFIGURED,
        odds_status=SOURCE_SKIPPED_NOT_CONFIGURED,
        enrichment_backend="none",
        odds_source=ODDS_SOURCE_NONE,
    ),
)
def test_telegram_shows_completeness_when_partial(_mock_enrich, mock_fs, _mock_url) -> None:
    facts = _botola_facts()
    mock_fs.return_value = (facts, {"flashscore": "ok"}, None)

    result = LiveFlashscorePipeline(persist=False).analyze_flashscore_url("https://example.com/m")
    text = format_telegram_match_reply(result)

    assert result.completeness is not None
    assert "Лучший рынок" in text
    if result.completeness.flashscore_missing:
        assert "ℹ️" in text or "⚠️" in text
