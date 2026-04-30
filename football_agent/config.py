import os

from dotenv import load_dotenv

load_dotenv()

FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"

LEAGUE_IDS_FOOTBALL_DATA = {
    "PL": 2021,
    "PD": 2014,
    "FL1": 2015,
    "BL1": 2002,
    "SA": 2019,
}

LEAGUE_IDS_API_FOOTBALL = {
    "PL": 39,
    "PD": 140,
    "FL1": 61,
    "BL1": 78,
    "SA": 135,
}

CURRENT_SEASON = 2024

TOTAL_ROUNDS = {"PL": 38, "PD": 38, "FL1": 34, "BL1": 34, "SA": 38}

RELEGATION_SLOTS = {"PL": 3, "PD": 3, "FL1": 3, "BL1": 3, "SA": 3}

EURO_SLOTS = {
    "PL": {"ucl": 4, "uel": 2},
    "PD": {"ucl": 4, "uel": 2},
    "FL1": {"ucl": 2, "uel": 3},
    "BL1": {"ucl": 4, "uel": 2},
    "SA": {"ucl": 4, "uel": 2},
}

FOOTBALL_DATA_REQUEST_DELAY = 6.5  # free tier: 10 req/min → 6s между запросами
API_FOOTBALL_REQUEST_DELAY = 1.0
CACHE_TTL_SECONDS = 6 * 3600  # 6 часов

EXPRESS_MIN_PROBABILITY = 0.72
EXPRESS_MIN_ODDS = 1.15
EXPRESS_MAX_ODDS = 2.8
