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
Для каждого матча ОБЯЗАТЕЛЬНО укажи рынок (best_pick_line или market_label), коэффициент и вероятность.
Формат строки: Команда1 — Команда2 — РЫНОК, кф X.XX, YY%.
Для all_matches НЕ добавляй экспресс, если include_express=false.
Для single_match покажи best_pick_line и top_picks (до 3 строк).
Для express: только события экспресса + итоговый коэфф."""


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


def _heuristic_parse(text: str, today: str) -> dict:
    low = text.lower().replace("ё", "е")
    parsed = {
        "type": "all_matches",
        "date": today,
        "target_odds": None,
        "home_team": None,
        "away_team": None,
    }
    if any(w in low for w in ("экспресс", "express", "купон", "паровоз")):
        parsed["type"] = "express"
    elif any(
        w in low
        for w in (
            "все матч",
            "все игр",
            "прогноз на все",
            "матчи на",
            "all matches",
            "fixtures",
        )
    ):
        parsed["type"] = "all_matches"
    import re

    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        parsed["date"] = m.group(1)
    km = re.search(r"кф\s*([0-9]+(?:[.,][0-9]+)?)", low)
    if km:
        parsed["target_odds"] = float(km.group(1).replace(",", "."))
    return parsed


def _apply_single_match_teams(text: str, parsed: dict) -> dict:
    if parsed.get("type") == "express":
        return parsed
    if parsed.get("home_team") and parsed.get("away_team"):
        return parsed
    low = text.strip()
    for sep in (" vs ", " v ", " — ", " - ", " – "):
        if sep in low:
            parts = low.split(sep, 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                parsed["type"] = "single_match"
                parsed["home_team"] = parts[0].strip()
                parsed["away_team"] = parts[1].strip()
                break
    return parsed


def parse_user_request(text: str) -> dict:
    from datetime import date

    today = date.today().isoformat()
    client = _get_openai_client()
    if client is None:
        parsed = _heuristic_parse(text, today)
        return _apply_single_match_teams(text, parsed)

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
        parsed = json.loads(content)
        parsed = _apply_parse_overrides(text, parsed)
        return _apply_single_match_teams(text, parsed)
    except Exception as e:
        logger.error(f"parse_user_request failed: {e}")
        parsed = _heuristic_parse(text, today)
        return _apply_single_match_teams(text, parsed)


def _apply_parse_overrides(text: str, parsed: dict) -> dict:
    """Keyword overrides so all_matches never becomes express by mistake."""
    low = text.lower().replace("ё", "е")
    if any(w in low for w in ("экспресс", "express", "купон")):
        parsed["type"] = "express"
    elif any(w in low for w in ("все матч", "прогноз на все", "матчи на", "all matches")):
        parsed["type"] = "all_matches"
    return parsed


def format_v2_or_llm(
    data: dict,
    request_type: str,
    deterministic_text: str,
) -> str:
    """Prefer deterministic v2 text; LLM only if API key set and user wants polish."""
    if deterministic_text:
        return deterministic_text
    return format_response(data, request_type)


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

