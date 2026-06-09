# API cache

JSON-ответы внешних API (football-data.org, API-Football).
Имена файлов: `md5(endpoint + str(params)).json`.

TTL задаётся в клиентах (`config.CACHE_TTL_SECONDS` и 12h для API-Football).
Содержимое `*.json` не коммитится.
