# Legacy runtime folder (do not use)

Эта папка в **корне репозитория** — артефакт запуска с рабочей директорией = корень проекта,
когда `Database` использовал относительный путь `data/football_agent.db`.

**Каноническое место для SQLite:** `football_agent/data/football_agent.db`

Если здесь есть `football_agent.db` с нужными прогнозами — скопируйте файл в каноническую папку
или удалите эту копию после проверки. Код с v1 foundation всегда пишет в `football_agent/data/`.
