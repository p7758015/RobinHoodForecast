# Структура проекта (канон v1 → foundation v2)

## Принципы

1. **Пакет приложения** — `football_agent/` (импорты: `from football_agent...`).
2. **Runtime-данные** — только внутри пакета: `football_agent/data/`, `football_agent/cache/`.
3. **Документация** — в корневой `docs/`.
4. **Аналитика** — чистый Python; LLM только parse/format.

## Канонические пути

| Назначение | Путь |
|------------|------|
| SQLite (прогнозы) | `football_agent/data/football_agent.db` |
| Снимки / дампы (v2) | `football_agent/data/snapshots/` |
| Кэш API (JSON) | `football_agent/cache/*.json` |
| Конфиг путей в коде | `football_agent/paths.py` |

Пути не зависят от текущей рабочей директории (CWD).

## v2 ingestion (feature flags)

| Источник | Условие | Код |
|----------|---------|-----|
| Legacy | `USE_V2_PIPELINE=true`, иначе или нет `OPENCLAW_BASE_URL` | `MatchSnapshotBuilder` + `LeagueAnalysisServiceV2` |
| OpenClaw | `USE_V2_PIPELINE=true` и `USE_OPENCLAW=true` и `OPENCLAW_BASE_URL` задан | `OpenClawLeagueAnalysisService` через `app_pipeline._create_v2_league_service` |

Telegram вызывает ``process_user_query`` **без** ``prefer_openclaw_ingestion`` (по умолчанию ``False``): v2 остаётся на legacy Football-Data + API-Football, даже если в ``.env`` включён OpenClaw для CLI.

## Устаревшее

- Корневая `data/` — появлялась при запуске с CWD=корень репозитория и относительным `data/football_agent.db`. **Не использовать.** См. `data/README.md` в корне.
- `football_agent/data/robinhood.sqlite` — база ранней итерации; можно удалить вручную после переноса данных, если нужно.
