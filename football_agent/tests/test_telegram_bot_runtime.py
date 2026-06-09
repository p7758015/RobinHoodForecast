"""Telegram bot runtime resilience tests (no real Telegram API)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

for _mod in ("telegram", "telegram.ext", "telegram.request"):
    sys.modules.setdefault(_mod, MagicMock())

from football_agent.bot import telegram_bot
from football_agent.bot.runtime_health import StartupReport


def test_run_startup_validation_raises_without_token() -> None:
    with patch.object(telegram_bot, "validate_startup") as mock_val:
        mock_val.return_value = StartupReport(ready=False, critical_errors=["no token"])
        try:
            telegram_bot.run_startup_validation()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "no token" in str(exc)


def test_run_startup_validation_ok_in_degraded_mode() -> None:
    report = StartupReport(
        ready=True,
        degraded_modes=["no_openclaw_context", "no_odds"],
    )
    with patch.object(telegram_bot, "validate_startup", return_value=report):
        out = telegram_bot.run_startup_validation(probe_dependencies=False)
    assert out.degraded_modes


def test_analysis_timeout_returns_user_message() -> None:
    async def _timeout_path() -> None:
        with patch.object(telegram_bot.config, "BOT_ANALYSIS_TIMEOUT_S", 0.05):

            def _block(_text: str) -> None:
                import time

                time.sleep(2)

            with patch.object(telegram_bot, "get_analysis_service") as mock_svc:
                mock_svc.return_value.analyze_text = _block
                resp = await telegram_bot._run_analysis("FAR — Rabat")

        assert resp.success is False
        assert resp.stage_failed == "analysis_timeout"
        assert "времени" in resp.reply_text.lower()

    asyncio.run(_timeout_path())


def test_message_handler_survives_analysis_exception() -> None:
    update = MagicMock()
    update.message.text = "hello"
    update.effective_user.id = 1
    update.effective_chat.id = 2
    context = MagicMock()

    async def _run() -> None:
        with patch.object(telegram_bot, "_run_analysis", side_effect=RuntimeError("boom")):
            await telegram_bot.message_handler(update, context)
        assert update.message.reply_text.called

    asyncio.run(_run())


def test_build_application_wires_error_handler() -> None:
    source = Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py"
    text = source.read_text(encoding="utf-8")
    assert "add_error_handler(error_handler)" in text
    assert "post_shutdown" in text
