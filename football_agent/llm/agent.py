from __future__ import annotations

import json
import logging

from football_agent.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

PARSE_SYSTEM = """Ты парсер запросов для футбольного бота. Верни ТОЛЬКО JSON без markdown.
Поля:
- type: "all_matches" | "express" | "single_match" | "stats"
- date: "YYYY-MM-DD" (вычисли из текста, сегодня = {today})
- target_odds: float или null
- home_team: str или null
- away_team: str или null"""

FORMAT_SYSTEM = """Ты ассистент футбольного аналитика. Формируй ответ на русском языке.
Используй ТОЛЬКО предоставленные данные, ничего не придумывай.
Формат для каждого матча: одна строка, эмодзи флага лиги, команды, котировка, вероятность в %.
Для экспресса: список событий + итоговый коэфф + итоговая вероятность."""


def _get_openai_client():
    try:
        import openai
    except Exception:
        return None

    if not OPENAI_API_KEY:
        return None
    try:
        return openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None


def parse_user_request(text: str) -> dict:
    from datetime import date

    today = date.today().isoformat()
    client = _get_openai_client()
    if client is None:
        return {
            "type": "all_matches",
            "date": today,
            "target_odds": None,
            "home_team": None,
            "away_team": None,
        }

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[
                {"role": "system", "content": PARSE_SYSTEM.format(today=today)},
                {"role": "user", "content": text},
            ],
        )
        content = resp.choices[0].message.content or ""
        return json.loads(content)
    except Exception as e:
        logger.error(f"parse_user_request failed: {e}")
        return {
            "type": "all_matches",
            "date": today,
            "target_odds": None,
            "home_team": None,
            "away_team": None,
        }


def format_response(data: dict, request_type: str) -> str:
    client = _get_openai_client()
    if client is None:
        return json.dumps(data, ensure_ascii=False, default=str, indent=2)

    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1500,
            messages=[
                {"role": "system", "content": FORMAT_SYSTEM},
                {"role": "user", "content": f"Тип запроса: {request_type}\nДанные: {payload}"},
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"format_response failed: {e}")
        return "Ошибка формирования ответа."

