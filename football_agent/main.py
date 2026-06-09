from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[1]))

import logging
import sys

from football_agent.app_pipeline import process_user_query
from football_agent.config import (
    API_FOOTBALL_KEY,
    FOOTBALL_DATA_API_KEY,
    OPENCLAW_BASE_URL,
    USE_OPENCLAW,
    USE_V2_PIPELINE,
)
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def run(user_text: str) -> str:
    prefer_oc = bool(USE_OPENCLAW and OPENCLAW_BASE_URL)
    if USE_V2_PIPELINE and prefer_oc:
        _log.info("CLI: v2 + OpenClaw ingestion (prefer_openclaw_ingestion=true)")
    elif USE_V2_PIPELINE:
        _log.info("CLI: v2 + legacy API ingestion (Football-Data + API-Football)")
    fd = FootballDataClient(FOOTBALL_DATA_API_KEY or "")
    af = ApiFootballClient(API_FOOTBALL_KEY or "")
    db = Database()
    return process_user_query(user_text, fd, af, db, prefer_openclaw_ingestion=prefer_oc)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Запрос: ")
    print(run(query))
