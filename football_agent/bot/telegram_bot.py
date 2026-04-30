# bot/telegram_bot.py

import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.request import HTTPXRequest

from football_agent.config import TELEGRAM_BOT_TOKEN, FOOTBALL_DATA_API_KEY, API_FOOTBALL_KEY
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.engine.match_analyzer import analyze_matches_for_date, analyze_single_match
from football_agent.engine.express_builder import build_express
from football_agent.storage.database import Database
from football_agent.llm.agent import parse_user_request, format_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "Привет! Я футбольный аналитический бот. "
    "Могу дать прогнозы на матчи топ-5 лиг или собрать экспресс.\n\n"
    "Примеры:\n"
    "- \"Дай прогноз на все матчи 25.04.2026\"\n"
    "- \"Собери экспресс кф 3.5 на сегодня\"\n"
    "- \"Челси – Ливерпуль завтра\""
)

# Инициализируем клиентов один раз
fd_client = FootballDataClient(FOOTBALL_DATA_API_KEY)
af_client = ApiFootballClient(API_FOOTBALL_KEY)
db = Database()


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    user_id = update.effective_user.id if update.effective_user else None
    logger.info("User %s: %s", user_id, user_text)

    # Показать "typing..."
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
    except Exception as e:
        logger.warning(f"send_chat_action failed: {e}")

    try:
        response = await _process_query(user_text)
    except Exception as e:
        logger.exception("Ошибка обработки запроса: %s", e)
        response = "Что-то пошло не так. Попробуй ещё раз или уточни запрос."

    # Telegram ограничивает длину сообщения
    if len(response) > 4096:
        response = response[:4090] + "\n..."

    try:
        await update.message.reply_text(response)
    except Exception as e:
        logger.exception("Ошибка отправки ответа в Telegram: %s", e)


async def _process_query(user_text: str) -> str:
    import asyncio
    loop = asyncio.get_event_loop()
    # Запускаем синхронное ядро в пуле потоков, чтобы не блокировать event loop
    return await loop.run_in_executor(None, _sync_process_query, user_text)


def _sync_process_query(user_text: str) -> str:
    """
    Синхронная логика, очень близкая к main.run().
    """
    req = parse_user_request(user_text)
    req_type = req.get("type", "all_matches")
    date_str = req.get("date")

    if req_type == "stats":
        report = db.get_accuracy_report()
        return format_response(report, "stats")

    if req_type == "single_match":
        result = analyze_single_match(
            req.get("home_team"),
            req.get("away_team"),
            date_str,
            fd_client,
            af_client,
        )
        if not result:
            return "Матч не найден. Уточни названия команд или дату."
        db.save_predictions([result])
        data = {
            "match": result.match.model_dump(),
            "markets": [m.model_dump() for m in result.markets],
        }
        return format_response(data, "single_match")

    results = analyze_matches_for_date(date_str, fd_client, af_client)
    if not results:
        return f"Матчей на {date_str} не найдено или данные ещё недоступны."

    if req_type == "express":
        target = float(req.get("target_odds") or 3.0)
        express = build_express(results, target_odds=target)

        # сохраняем как экспресс-прогнозы
        db.save_predictions(
            results=[r for r in results if r.match.id in {e.match.id for e in express.events}],
            is_express=True,
        )

        data = {
            "events": [
                {"match": e.match.model_dump(), "market": e.market.model_dump()}
                for e in express.events
            ],
            "total_odds": express.total_odds,
            "total_probability": express.total_probability,
            "target_odds": express.target_odds,
        }
        return format_response(data, "express")

    # all_matches
    db.save_predictions(results)
    data = {
        "date": date_str,
        "matches": [
            {
                "match": f"{r.match.home_team.short_name} vs {r.match.away_team.short_name}",
                "competition": r.match.competition_code,
                "best_market": r.best_market.model_dump(),
                "top3": [m.model_dump() for m in r.markets[:3]],
            }
            for r in results
        ],
    }
    return format_response(data, "all_matches")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    # Увеличенные таймауты для запросов к Telegram
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Telegram бот запущен. Ожидаю сообщения...")
    app.run_polling()


if __name__ == "__main__":
    main()