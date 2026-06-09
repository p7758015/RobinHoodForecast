"""Startup validation and health reporting tests."""

from __future__ import annotations

from unittest.mock import patch

from football_agent.bot.runtime_health import format_health_message, validate_startup


def test_startup_fails_without_telegram_token() -> None:
    with patch("football_agent.bot.runtime_health.config.TELEGRAM_BOT_TOKEN", None):
        report = validate_startup(probe_dependencies=False)
    assert report.ready is False
    assert any("TELEGRAM_BOT_TOKEN" in e for e in report.critical_errors)


def test_degraded_startup_without_flashscore() -> None:
    with patch("football_agent.bot.runtime_health.config.TELEGRAM_BOT_TOKEN", "tok"):
        with patch("football_agent.bot.runtime_health.config.FLASHSCORE_SCRAPER_URL", None):
            with patch("football_agent.bot.runtime_health.config.OPENCLAW_CONTEXT_BASE_URL", None):
                with patch("football_agent.bot.runtime_health.config.ODDS_SERVICE_URL", None):
                    with patch("football_agent.bot.runtime_health.ping_database", return_value=True):
                        report = validate_startup(probe_dependencies=False)
    assert report.ready is True
    assert "no_flashscore" in report.degraded_modes
    assert "no_openclaw_enrichment" in report.degraded_modes


def test_health_message_lists_degraded() -> None:
    with patch("football_agent.bot.runtime_health.config.TELEGRAM_BOT_TOKEN", "tok"):
        with patch("football_agent.bot.runtime_health.config.FLASHSCORE_SCRAPER_URL", "http://fs"):
            with patch("football_agent.bot.runtime_health._probe_http_health", return_value=True):
                with patch("football_agent.bot.runtime_health.ping_database", return_value=True):
                    with patch("football_agent.bot.runtime_health.open_sqlite_connection") as mock_open:
                        mock_open.return_value.execute.return_value.fetchone.return_value = ("wal",)
                        mock_open.return_value.close = lambda: None
                        text = format_health_message()
    assert "flashscore" in text
    assert "Degraded" in text


def test_unreachable_flashscore_adds_degraded_mode() -> None:
    with patch("football_agent.bot.runtime_health.config.TELEGRAM_BOT_TOKEN", "tok"):
        with patch("football_agent.bot.runtime_health.config.FLASHSCORE_SCRAPER_URL", "http://fs"):
            with patch("football_agent.bot.runtime_health.config.OPENCLAW_CONTEXT_BASE_URL", None):
                with patch("football_agent.bot.runtime_health.config.ODDS_SERVICE_URL", None):
                    with patch("football_agent.bot.runtime_health._probe_http_health", return_value=False):
                        with patch("football_agent.bot.runtime_health.ping_database", return_value=True):
                            report = validate_startup(probe_dependencies=True)
    assert "flashscore_unreachable" in report.degraded_modes
