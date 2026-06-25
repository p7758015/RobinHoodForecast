"""Brave / OpenClaw news enrichment tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.merge.news_merge import merge_news_into_merged_context
from football_agent.news_context.extraction import extract_coach_block, extract_general_news_block
from football_agent.news_context.models import CoachContextBlock, MatchNewsContext
from football_agent.news_context.query_builder import build_match_news_queries
from football_agent.services.brave_search_client import BraveSearchClient, BraveSearchHit
from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.tests.test_analysis_merge_layer import _facts as merge_facts


def _facts() -> FlashscoreMatchFacts:
    return FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="m1",
            source_url="https://example.invalid/m1",
            home_team_name="Mexico",
            away_team_name="South Africa",
            competition_name="Friendly",
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )


def test_brave_client_auth_header() -> None:
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"web": {"results": [{"title": "A", "url": "https://x.com", "description": "d"}]}},
    )
    client = BraveSearchClient(api_key="test-key", session=session)
    client.search("Mexico South Africa preview")
    _args, kwargs = session.get.call_args
    assert kwargs["headers"]["X-Subscription-Token"] == "test-key"


def test_brave_client_non_json_response_raises_clear_error() -> None:
    session = MagicMock()
    bad = MagicMock(
        status_code=200,
        headers={"content-type": "text/html"},
        text="<html>not json</html>",
    )
    bad.json.side_effect = ValueError("Expecting value")
    session.get.return_value = bad
    client = BraveSearchClient(api_key="k", session=session)
    from football_agent.services.brave_search_client import BraveSearchUnavailableError

    with pytest.raises(BraveSearchUnavailableError, match="non-JSON"):
        client.search("test query")


def test_brave_client_empty_query() -> None:
    client = BraveSearchClient(api_key="k")
    assert client.search("  ") == []


def test_normalize_brave_search_lang_pt_alias() -> None:
    from football_agent.services.brave_search_client import normalize_brave_search_lang

    assert normalize_brave_search_lang("pt") == "pt-br"
    assert normalize_brave_search_lang("PT-BR") == "pt-br"
    assert normalize_brave_search_lang("en") == "en"


def test_filter_hits_accent_fold_team_match() -> None:
    from datetime import datetime, timezone
    from football_agent.services.brave_search_client import BraveSearchHit, filter_hits_by_lookback

    hit = BraveSearchHit(
        title="Palpite América-MG x Criciúma Série B",
        description="preview do jogo",
        topic_tags=["preview"],
        published_at=datetime.now(timezone.utc),
    )
    kept = filter_hits_by_lookback(
        [hit],
        lookback_hours=72,
        home_team="America MG",
        away_team="Criciuma",
    )
    assert len(kept) == 1


def test_brave_base_url_normalizes_bare_host(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_BASE_URL", "https://api.search.brave.com")
    from importlib import reload
    import football_agent.config as cfg

    reload(cfg)
    assert cfg.BRAVE_SEARCH_BASE_URL.endswith("/res/v1/web/search")


def test_brave_client_retry_on_failure() -> None:
    session = MagicMock()
    session.get.side_effect = [
        requests.Timeout("timeout"),
        MagicMock(
            status_code=200,
            json=lambda: {"web": {"results": []}},
        ),
    ]
    client = BraveSearchClient(api_key="k", session=session)
    assert client.search("test") == []
    assert session.get.call_count == 2


def test_query_builder_dedup_and_coach_pass() -> None:
    team_only = build_match_news_queries(home_team="Mexico", away_team="South Africa")
    assert any(q.category == "preview" for q in team_only)
    assert len(team_only) == len({q.query.lower() for q in team_only})

    coach = build_match_news_queries(
        home_team="Mexico",
        away_team="South Africa",
        home_coach_name="Juan Carlos Osorio",
        away_coach_name="Hugo Broos",
    )
    assert any(q.category == "h2h" for q in coach)
    assert any("press conference" in q.query for q in coach)


def test_query_builder_brazil_serie_b_locale() -> None:
    queries = build_match_news_queries(
        home_team="America MG",
        away_team="Criciuma",
        competition_name="Serie B",
        competition_country="Brazil",
    )
    joined = " ".join(q.query.lower() for q in queries)
    assert "série b" in joined or "serie b" in joined
    assert "escalação" in joined or "tecnico" in joined or "técnico" in joined


def test_extraction_coach_signals() -> None:
    hits = [
        BraveSearchHit(
            title="Mexico coach rotation expected",
            description='Manager said "we will rotate the squad" amid fixture congestion. Met 4 times head-to-head.',
            topic_tags=["coach"],
            published_at=datetime.now(timezone.utc),
        ),
    ]
    block = extract_coach_block(
        hits=hits,
        home_team="Mexico",
        away_team="South Africa",
        home_coach_hint="Juan Carlos Osorio",
    )
    assert block.home_coach_rotation_signal is not None
    assert block.coach_h2h_total_matches == 4
    assert block.coach_news_confidence > 0


def test_extraction_general_injuries() -> None:
    hits = [
        BraveSearchHit(
            title="South Africa injury doubt",
            description="Key striker doubtful for friendly injury concern.",
            topic_tags=["injuries"],
        ),
    ]
    g = extract_general_news_block(hits=hits, home_team="Mexico", away_team="South Africa")
    assert g.injuries_signals
    assert g.general_news_confidence > 0


def test_extraction_empty_fail_soft() -> None:
    block = extract_coach_block(hits=[], home_team="A", away_team="B")
    assert block.coach_news_confidence == 0.0
    assert "coach_news_no_results" in block.warnings


@patch("football_agent.services.openclaw_news_enrichment.brave_news_enabled", return_value=True)
@patch("football_agent.services.openclaw_news_enrichment.BraveSearchClient")
def test_enrich_match_news_mocked(mock_client_cls, _enabled) -> None:
    mock_client = MagicMock()
    mock_client.configured = True
    mock_client.search.return_value = [
        BraveSearchHit(
            title="Mexico vs South Africa preview",
            description="Mexico manager expects rotation. Injury doubt for South Africa.",
            url="https://news.example/mex-sou",
            topic_tags=["preview"],
            published_at=datetime.now(timezone.utc),
        ),
    ]
    mock_client_cls.return_value = mock_client

    with patch("football_agent.services.openclaw_news_enrichment.config") as cfg:
        cfg.USE_OPENCLAW_COACH_CONTEXT = True
        cfg.USE_OPENCLAW_NEWS = True
        cfg.BRAVE_SEARCH_MAX_RESULTS = 8
        cfg.BRAVE_NEWS_MAX_ARTICLES_PER_MATCH = 10
        cfg.BRAVE_NEWS_LOOKBACK_HOURS = 72
        cfg.BRAVE_NEWS_INCLUDE_COACH_TERMS = True
        cfg.BRAVE_NEWS_INCLUDE_INJURY_TERMS = True
        cfg.BRAVE_NEWS_INCLUDE_LINEUP_TERMS = True
        cfg.BRAVE_COACH_H2H_LOOKBACK_DAYS = 365
        cfg.OPENCLAW_FAIL_SOFT = True
        from football_agent.services.openclaw_news_enrichment import enrich_match_news_from_brave

        news = enrich_match_news_from_brave(_facts(), client=mock_client)

    assert news is not None
    assert news.source_count >= 1
    assert news.confidence >= 0


def test_merge_additive_factual_untouched() -> None:
    facts = merge_facts()
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    home_before = merged.flashscore_facts.meta.home_team_name

    news = MatchNewsContext(
        home_team="X",
        coach=CoachContextBlock(home_coach_name="Coach X"),
        confidence=0.5,
        collected_at_utc=datetime.now(timezone.utc),
    )
    out = merge_news_into_merged_context(merged, news)
    assert out.flashscore_facts.meta.home_team_name == home_before
    assert out.news_context is not None
    assert "news_enrichment" in out.provenance.blocks_present


@patch("football_agent.services.openclaw_news_enrichment.brave_news_enabled", return_value=False)
def test_enrichment_skips_when_disabled(_mock) -> None:
    from football_agent.services.enrichment_live import _fetch_brave_news_if_enabled

    warnings: list[str] = []
    sources: dict[str, str] = {}
    result = _fetch_brave_news_if_enabled(_facts(), None, warnings, sources)
    assert result is None
    assert sources.get("brave_news") == "skipped_not_configured"


def test_brave_news_enabled_master_flag_only() -> None:
    from football_agent.services.openclaw_news_enrichment import brave_news_enabled

    with patch("football_agent.services.openclaw_news_enrichment.config") as cfg:
        cfg.USE_BRAVE_NEWS_ENRICHMENT = True
        cfg.USE_OPENCLAW_NEWS = False
        cfg.USE_OPENCLAW_COACH_CONTEXT = False
        cfg.BRAVE_SEARCH_API_KEY = "key"
        assert brave_news_enabled() is True


def test_brave_news_enabled_legacy_flags_without_master() -> None:
    from football_agent.services.openclaw_news_enrichment import brave_news_enabled

    with patch("football_agent.services.openclaw_news_enrichment.config") as cfg:
        cfg.USE_BRAVE_NEWS_ENRICHMENT = False
        cfg.USE_OPENCLAW_NEWS = True
        cfg.USE_OPENCLAW_COACH_CONTEXT = False
        cfg.BRAVE_SEARCH_API_KEY = "key"
        assert brave_news_enabled() is True


def test_brave_coach_and_general_enabled_with_master() -> None:
    from football_agent.services.openclaw_news_enrichment import (
        brave_coach_context_enabled,
        brave_general_news_enabled,
    )

    with patch("football_agent.services.openclaw_news_enrichment.config") as cfg:
        cfg.USE_BRAVE_NEWS_ENRICHMENT = True
        cfg.USE_OPENCLAW_NEWS = False
        cfg.USE_OPENCLAW_COACH_CONTEXT = False
        assert brave_coach_context_enabled() is True
        assert brave_general_news_enabled() is True


def test_merge_removes_news_context_from_missing() -> None:
    facts = merge_facts()
    merged = merge_match_context_v2(facts=facts, openclaw_context=None, odds_context=None)
    assert "news_context" in merged.provenance.missing_blocks

    news = MatchNewsContext(
        home_team="X",
        coach=CoachContextBlock(home_coach_name="Coach X"),
        confidence=0.5,
        source_count=1,
        collected_at_utc=datetime.now(timezone.utc),
    )
    out = merge_news_into_merged_context(merged, news)
    assert out.news_context is not None
    assert "news_context" in out.provenance.blocks_present
    assert "news_context" not in out.provenance.missing_blocks
