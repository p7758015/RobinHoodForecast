# RobinHoodForecast / football_agent

## 1) Описание
**RobinHoodForecast** — ядро футбольного аналитического сервиса и CLI-бота.
Сервис анализирует матчи топ‑5 лиг:
- Англия (PL)
- Испания (PD)
- Франция (FL1)
- Германия (BL1)
- Италия (SA)

Поддерживаемые типы запросов:
- **all_matches**: анализ всех матчей на дату
- **single_match**: анализ конкретного матча на дату
- **express**: сбор экспресса на целевой коэффициент
- **stats**: отчёт по точности (winrate / калибровка и т.д.) из SQLite

LLM используется **строго** для:
1) парсинга текстового запроса пользователя в структурированный JSON  
2) форматирования уже посчитанных данных в читаемый текст  
Вся аналитика/математика реализована в чистом Python.

## 2) Установка

Требуется Python **3.11+**.

Установить зависимости:

```bash
pip install requests pydantic openai python-dotenv
```

Создать `.env` по примеру:
- скопируй `football_agent/.env.example` → `football_agent/.env`
- (опционально) также можно создать `.env` в корне проекта

## 3) Запуск

Пример:

```bash
python main.py "Дай прогноз на все матчи 25.04.2026"
```

## 4) Закрытие прогнозов (settle)

Сохраняет фактические результаты матчей и закрывает прогнозы в базе:

```bash
python settle_results.py --date 2026-04-25
```

## 5) Переменные окружения

| Переменная | Описание |
|---|---|
| `FOOTBALL_DATA_API_KEY` | API key для `football-data.org` (v4), заголовок `X-Auth-Token` |
| `API_FOOTBALL_KEY` | API key для `API-Football` (API-Sports v3), заголовок `x-apisports-key` |
| `OPENAI_API_KEY` | API key для OpenAI (используется только для parse/format) |

## 6) Архитектура

- `config.py`: ключи, базовые URL, словари лиг, параметры rate-limit и кэша, пороги экспресса
- `data_providers/football_data_client.py`: клиент `football-data.org` с кэшем/TTL и throttling
- `data_providers/api_football_client.py`: клиент `API-Football` с кэшем/TTL + odds parsing + fuzzy fixture match
- `domain/models.py`: доменные модели (Pydantic v2)
- `domain/features.py`: расчёт факторов (мотивация/форма/тренер/H2H)
- `domain/probability_model.py`: базовая модель вероятностей рынков + выбор лучших маркетов
- `engine/match_analyzer.py`: пайплайн анализа матчей на дату / поиск конкретного матча
- `engine/express_builder.py`: построение экспресса под целевой коэффициент
- `storage/database.py`: SQLite-хранилище прогнозов, результатов, settle и отчёты
- `llm/agent.py`: тонкий LLM-слой (parse/format)

## 7) Лимиты API

- `football-data.org` (free): **10 req/min** → ~6 секунд между запросами
- `api-football` (free): **100 req/day** → кэш обязателен (TTL 12 часов)

## Финальная проверка

```bash
python -c "from domain.models import *; from domain.features import *; from domain.probability_model import *; print('imports OK')"
python test_run.py
python main.py \"Дай прогноз на все матчи которые пройдут 25.04.2026\"
```

Примечания:
- Третья команда требует реальные API ключи в `.env`.
- Если `OPENAI_API_KEY` не задан, ответ будет возвращён в JSON (fallback), без LLM‑форматирования.

