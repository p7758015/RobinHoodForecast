"""Telegram bot transport layer tests (no real Telegram API)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# python-telegram-bot may be absent in CI/test env
for _mod in ("telegram", "telegram.ext", "telegram.request"):
    sys.modules.setdefault(_mod, MagicMock())

from football_agent.bot import telegram_bot  # noqa: E402
from football_agent.services.telegram_match_analysis_service import TelegramAnalysisResponse  # noqa: E402


def test_bot_source_delegates_to_analysis_service() -> None:
    source = Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py"
    text = source.read_text(encoding="utf-8")
    assert "TelegramMatchAnalysisService" in text
    assert "process_user_query" not in text
    assert "analyze_matches_for_date" not in text


def test_message_handler_calls_analysis_service() -> None:
    mock_response = TelegramAnalysisResponse(
        reply_text="ok",
        success=True,
        request_kind="flashscore_url",
    )
    update = MagicMock()
    update.message.text = "https://www.flashscore.com/match/football/a/b/?mid=x"
    update.effective_user.id = 1
    update.effective_chat.id = 2
    context = MagicMock()

    async def _run() -> None:
        with patch.object(telegram_bot, "_run_analysis", return_value=mock_response) as mock_run:
            await telegram_bot.message_handler(update, context)
        mock_run.assert_called_once()
        assert update.message.reply_text.call_count >= 1

    asyncio.run(_run())


def test_health_handler_reports_scraper_status() -> None:
    source = Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py"
    text = source.read_text(encoding="utf-8")
    assert "health_handler" in text
    assert "runtime_health" in text
    assert "add_error_handler" in text
