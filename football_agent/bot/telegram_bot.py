# bot/telegram_bot.py

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from football_agent.app_pipeline import pipeline_label, process_user_query
from football_agent.config import API_FOOTBALL_KEY, FOOTBALL_DATA_API_KEY, TELEGRAM_BOT_TOKEN
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "Привет! Я футбольный аналитический бот. "
    "Могу дать прогнозы на матчи лиг из каталога или собрать экспресс.\n\n"
    "Примеры:\n"
    "- \"Дай прогноз на все матчи 25.04.2026\"\n"
    "- \"Собери экспресс кф 3.5 на сегодня\"\n"
    "- \"Челси – Ливерпуль завтра\""
)

fd_client = FootballDataClient(FOOTBALL_DATA_API_KEY or "")
af_client = ApiFootballClient(API_FOOTBALL_KEY or "")
db = Database()


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    user_id = update.effective_user.id if update.effective_user else None
    logger.info("User %s (pipeline=%s): %s", user_id, pipeline_label(), user_text)

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing",
        )
    except Exception as e:
        logger.warning("send_chat_action failed: %s", e)

    try:
        response = await _process_query(user_text)
    except Exception as e:
        logger.exception("Ошибка обработки запроса: %s", e)
        response = "Что-то пошло не так. Попробуй ещё раз или уточни запрос."

    if len(response) > 4096:
        response = response[:4090] + "\n..."

    try:
        await update.message.reply_text(response)
    except Exception as e:
        logger.exception("Ошибка отправки ответа в Telegram: %s", e)


async def _process_query(user_text: str) -> str:
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_process_query, user_text)


def _sync_process_query(user_text: str) -> str:
    return process_user_query(user_text, fd_client, af_client, db)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Telegram бот запущен (pipeline=%s). Ожидаю сообщения...", pipeline_label())
    app.run_polling()


if __name__ == "__main__":
    main()
