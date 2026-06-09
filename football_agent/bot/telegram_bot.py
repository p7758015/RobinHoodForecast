# bot/telegram_bot.py
"""
Telegram transport layer (long polling, 24/7-ready).

Business logic lives in ``TelegramMatchAnalysisService`` — this module only
routes updates, sends replies, and logs outcomes.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from football_agent import config
from football_agent.bot.runtime_health import (
    StartupReport,
    format_health_message,
    validate_startup,
)
from football_agent.paths import DEFAULT_DB_PATH
from football_agent.services.live_flashscore_pipeline import LiveFlashscorePipeline
from football_agent.services.telegram_match_analysis_service import (
    TelegramAnalysisResponse,
    TelegramMatchAnalysisService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "Привет! Я аналитический бот RobinHood Forecast.\n\n"
    "Отправьте ссылку на матч Flashscore или пары команд:\n"
    "• https://www.flashscore.com/match/football/.../?mid=...\n"
    "• FAR Rabat — Maghreb Fez\n"
    "• Real Madrid vs Barcelona 2026-06-15\n\n"
    "Команды: /help /health"
)

HELP_TEXT = (
    "Как пользоваться:\n\n"
    "1. Ссылка Flashscore — самый надёжный способ.\n"
    "2. Команды через «—», «-» или «vs» (дата опциональна).\n\n"
    "Примеры:\n"
    "https://www.flashscore.com/match/football/.../?mid=dC2J6FlK\n"
    "Kawkab Marrakech — Raja Casablanca\n\n"
    "Сейчас поддерживается анализ одного матча.\n"
    "Проверка сервисов: /health"
)

_startup_report: Optional[StartupReport] = None
_analysis_service: Optional[TelegramMatchAnalysisService] = None


def get_startup_report() -> StartupReport:
    global _startup_report
    if _startup_report is None:
        _startup_report = validate_startup(probe_dependencies=True)
    return _startup_report


def build_analysis_service() -> TelegramMatchAnalysisService:
    pipeline = LiveFlashscorePipeline(
        db_path=DEFAULT_DB_PATH,
        persist=True,
    )
    return TelegramMatchAnalysisService(pipeline=pipeline)


def get_analysis_service() -> TelegramMatchAnalysisService:
    global _analysis_service
    if _analysis_service is None:
        _analysis_service = build_analysis_service()
    return _analysis_service


def run_startup_validation(*, probe_dependencies: bool = True) -> StartupReport:
    """Validate config/deps before long polling. Raises if not ready."""
    global _startup_report
    _startup_report = validate_startup(probe_dependencies=probe_dependencies)
    _startup_report.log_summary()
    if not _startup_report.ready:
        raise RuntimeError(
            "Bot startup failed: " + "; ".join(_startup_report.critical_errors),
        )
    return _startup_report


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    report = validate_startup(probe_dependencies=True)
    await update.message.reply_text(format_health_message(report))


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        logger.warning("message_handler: update without message")
        return

    user_text = update.message.text or ""
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    logger.info(
        "inbound user_id=%s chat_id=%s len=%d text=%r",
        user_id,
        chat_id,
        len(user_text),
        user_text[:200],
    )

    try:
        if chat_id is not None:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception as exc:
        logger.warning("send_chat_action failed user_id=%s: %s", user_id, exc)

    if _looks_like_analysis_request(user_text):
        try:
            await update.message.reply_text("Анализирую матч, подождите…")
        except Exception as exc:
            logger.warning("progress message failed user_id=%s: %s", user_id, exc)

    try:
        response = await _run_analysis(user_text)
        logger.info(
            "analysis_done user_id=%s success=%s kind=%s path=%s stage=%s persisted=%s",
            user_id,
            response.success,
            response.request_kind,
            response.analysis_path,
            response.stage_failed,
            response.persisted,
        )
    except Exception as exc:
        logger.exception("Unhandled analysis error user_id=%s: %s", user_id, exc)
        response = TelegramAnalysisResponse(
            reply_text="Что-то пошло не так. Попробуйте позже или пришлите ссылку Flashscore.",
            success=False,
            request_kind="error",
            stage_failed="unhandled",
        )

    text = response.reply_text
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    try:
        await update.message.reply_text(text)
    except Exception as exc:
        logger.exception("Failed to send Telegram reply user_id=%s: %s", user_id, exc)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception(
        "Telegram handler error update=%r: %s",
        update,
        context.error,
    )


async def _on_post_init(application: Application) -> None:  # noqa: ARG001
    report = get_startup_report()
    logger.info(
        "Bot post_init ready degraded=%s db=%s",
        report.degraded_modes or "none",
        DEFAULT_DB_PATH,
    )


async def _on_post_shutdown(application: Application) -> None:  # noqa: ARG001
    logger.info("Bot shutting down gracefully")


def _looks_like_analysis_request(text: str) -> bool:
    low = text.lower()
    return "flashscore" in low or any(sep in text for sep in (" — ", " - ", " vs ", " v ", " – "))


async def _run_analysis(user_text: str) -> TelegramAnalysisResponse:
    loop = asyncio.get_running_loop()
    service = get_analysis_service()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, service.analyze_text, user_text),
            timeout=config.BOT_ANALYSIS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error("Analysis timed out after %.0fs text=%r", config.BOT_ANALYSIS_TIMEOUT_S, user_text[:200])
        return TelegramAnalysisResponse(
            reply_text=(
                "Анализ занял слишком много времени.\n"
                "Попробуйте позже или пришлите ссылку Flashscore на конкретный матч."
            ),
            success=False,
            request_kind="timeout",
            stage_failed="analysis_timeout",
        )


def build_application() -> Application:
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=60.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN or "")
        .request(request)
        .post_init(_on_post_init)
        .post_shutdown(_on_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("health", health_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    report = run_startup_validation(probe_dependencies=True)
    logger.info(
        "Telegram bot starting (long polling) pid=%s degraded=%s",
        "n/a",
        report.degraded_modes or "none",
    )
    app = build_application()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
