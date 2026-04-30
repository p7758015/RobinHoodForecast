"""
Запуск: python settle_results.py --date 2026-04-25
Закрывает прогнозы по фактическим результатам матчей.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[1]))

import argparse
import logging
from datetime import date, timedelta

from football_agent.config import FOOTBALL_DATA_API_KEY
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.storage.database import Database

logging.basicConfig(level=logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today() - timedelta(days=1)))
    args = parser.parse_args()

    fd = FootballDataClient(FOOTBALL_DATA_API_KEY or "")
    db = Database()

    finished = fd.get_finished_matches_by_date(args.date)

    saved = 0
    for m in finished:
        if m.home_score is not None and m.away_score is not None:
            db.save_match_result(
                args.date,
                m.home_team.name,
                m.away_team.name,
                int(m.home_score),
                int(m.away_score),
            )
            saved += 1

    settled = db.settle_predictions()
    print(f"Сохранено результатов: {saved}, закрыто прогнозов: {settled}")

    report = db.get_accuracy_report()
    overall = report.get("overall", {})
    wins = overall.get("wins") or 0
    total = overall.get("total") or 0
    winrate = overall.get("winrate") or 0.0
    print(f"Общий winrate: {winrate:.1%} ({wins}/{total})")


if __name__ == "__main__":
    main()

