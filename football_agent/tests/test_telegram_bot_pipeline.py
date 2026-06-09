"""Telegram bot uses centralized app pipeline (no inline v1 orchestration)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# python-telegram-bot may be absent in CI/test env
for _mod in ("telegram", "telegram.ext", "telegram.request"):
    sys.modules.setdefault(_mod, MagicMock())

from football_agent.bot import telegram_bot  # noqa: E402


def test_sync_process_query_delegates_to_pipeline() -> None:
    with patch("football_agent.bot.telegram_bot.process_user_query", return_value="ok") as mock_proc:
        out = telegram_bot._sync_process_query("экспресс кф 3")
    assert out == "ok"
    mock_proc.assert_called_once_with("экспресс кф 3", telegram_bot.fd_client, telegram_bot.af_client, telegram_bot.db)


def test_bot_source_has_no_inline_match_analyzer() -> None:
    source = Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py"
    text = source.read_text(encoding="utf-8")
    assert "analyze_matches_for_date" not in text
    assert "build_express" not in text
    assert "analyze_single_match" not in text
    assert "process_user_query" in text
