"""User-facing clarification prompts with valid request examples."""

from __future__ import annotations

from football_agent.bot.request_parser import ClarificationReason


_MATCH_EXAMPLES = (
    "дай прогноз на Реал Мадрид — Валенсия",
    "прогноз на Арсенал — Челси 2026-08-21",
    "https://www.flashscore.com/match/football/.../?mid=...",
)

_LEAGUE_EXAMPLES = (
    "дай прогноз на лигу Китая",
    "проанализируй следующий тур латвии",
    "дай прогноз на серию B бразилии на завтра",
)


def format_clarification_reply(reason: ClarificationReason) -> str:
    if reason == ClarificationReason.TOO_VAGUE:
        return _too_vague()
    if reason == ClarificationReason.MISSING_LEAGUE:
        return _missing_league()
    if reason == ClarificationReason.MISSING_MATCH_TEAMS:
        return _missing_match()
    if reason == ClarificationReason.MISSING_OPPONENT:
        return _missing_opponent()
    if reason == ClarificationReason.AMBIGUOUS_TEAMS:
        return _ambiguous_teams()
    if reason == ClarificationReason.DATE_AMBIGUOUS:
        return _date_ambiguous()
    if reason == ClarificationReason.AMBIGUOUS_LEAGUE:
        return _ambiguous_league_generic()
    return _unsupported_fallback()


def format_ambiguous_league_reply(candidates: list) -> str:
    opts = "\n".join(
        f"• {c.competition_name} ({c.country or '?'})"
        for c in candidates[:5]
    )
    lines = [
        "Нашёл несколько лиг по запросу — уточните, какую именно:",
        opts,
        "",
        "Напишите полное название или страну, например:",
        "• Chinese Super League",
        "• лига Китая (высшая)",
        "• J1 League Japan",
    ]
    return "\n".join(lines)


def _too_vague() -> str:
    return (
        "Запрос слишком общий — не могу понять, матч или лига.\n\n"
        "Уточните:\n"
        "• матч: обе команды (или ссылка Flashscore)\n"
        "• лига: название чемпионата и период\n\n"
        "Примеры:\n"
        + _examples_block(_MATCH_EXAMPLES[:2] + (_LEAGUE_EXAMPLES[0],))
    )


def _missing_league() -> str:
    return (
        "Не указана лига или турнир.\n\n"
        "Напишите, какую лигу и на какой период анализировать.\n\n"
        "Примеры:\n"
        + _examples_block(_LEAGUE_EXAMPLES)
    )


def _missing_match() -> str:
    return (
        "Не указан матч.\n\n"
        "Нужны обе команды через «—» / «vs» или ссылка Flashscore.\n\n"
        "Примеры:\n"
        + _examples_block(_MATCH_EXAMPLES)
    )


def _missing_opponent() -> str:
    return (
        "Указана одна команда — не хватает соперника и/или даты.\n\n"
        "Напишите соперника или полную пару команд.\n\n"
        "Примеры:\n"
        "• с Миланом завтра\n"
        "• Интер — Милан 2026-06-15\n"
        + _examples_block(_MATCH_EXAMPLES[:1])
    )


def _ambiguous_teams() -> str:
    return (
        "Похоже на две команды, но формат неясен.\n\n"
        "Используйте разделитель «—», «-» или «vs» между командами.\n\n"
        "Примеры:\n"
        + _examples_block(_MATCH_EXAMPLES[:2])
    )


def _date_ambiguous() -> str:
    return (
        "Период дат неоднозначен — укажите конкретную дату в формате ГГГГ-ММ-ДД.\n\n"
        "Примеры:\n"
        "• дай прогноз на лигу Китая на 2026-06-15\n"
        "• Арсенал — Челси 2026-06-15\n"
        "• проанализируй следующий тур латвии 2026-06-20"
    )


def _ambiguous_league_generic() -> str:
    return (
        "Запрос по лиге неоднозначен — уточните название и страну.\n\n"
        "Примеры:\n"
        + _examples_block(_LEAGUE_EXAMPLES)
    )


def _unsupported_fallback() -> str:
    return (
        "Не удалось разобрать запрос.\n\n"
        "Поддерживается:\n"
        "• ссылка Flashscore на матч\n"
        "• пара команд через «—» или «vs»\n"
        "• запрос по лиге с названием чемпионата\n\n"
        "Примеры:\n"
        + _examples_block(_MATCH_EXAMPLES[:2] + (_LEAGUE_EXAMPLES[0],))
    )


def _examples_block(examples: tuple[str, ...]) -> str:
    return "\n".join(f"• `{ex}`" for ex in examples)
