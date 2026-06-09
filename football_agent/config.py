import os

from dotenv import load_dotenv

from football_agent.league_registry import (
    build_euro_slots,
    build_league_ids_api_football,
    build_league_ids_football_data,
    build_relegation_slots,
    build_total_rounds,
)
from football_agent.paths import CACHE_DIR, DATA_DIR, DEFAULT_DB_PATH, SNAPSHOTS_DIR

load_dotenv()

FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# v2 league pipeline (builder + scorer + express); default off — v1 unchanged
USE_V2_PIPELINE = os.getenv("USE_V2_PIPELINE", "false").strip().lower() in ("1", "true", "yes", "on")

# OpenClaw legacy match payload path (app_pipeline optional ingestion)
USE_OPENCLAW = os.getenv("USE_OPENCLAW", "false").strip().lower() in ("1", "true", "yes", "on")
# Primary unified enrichment backend (context + odds) — target v1; optional until deployed
OPENCLAW_BASE_URL = (os.getenv("OPENCLAW_BASE_URL") or "").strip().rstrip("/") or None
OPENCLAW_API_KEY = os.getenv("OPENCLAW_API_KEY")

# OpenClaw context — legacy alias for OPENCLAW_BASE_URL (backward compatible)
OPENCLAW_CONTEXT_BASE_URL = (os.getenv("OPENCLAW_CONTEXT_BASE_URL") or "").strip().rstrip("/") or None
OPENCLAW_CONTEXT_API_KEY = os.getenv("OPENCLAW_CONTEXT_API_KEY") or OPENCLAW_API_KEY
OPENCLAW_CONTEXT_TIMEOUT_S = float(os.getenv("OPENCLAW_CONTEXT_TIMEOUT_S", "30"))
# split = /v1/context + /v1/odds; unified = /v1/enrichment (future-ready)
OPENCLAW_ENRICHMENT_MODE = (os.getenv("OPENCLAW_ENRICHMENT_MODE") or "split").strip().lower()
OPENCLAW_PROVIDES_ODDS = os.getenv("OPENCLAW_PROVIDES_ODDS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Self-hosted Flashscore scraper (Telegram bot + debug/CLI)
FLASHSCORE_SCRAPER_URL = (os.getenv("FLASHSCORE_SCRAPER_URL") or "").strip().rstrip("/") or None
FLASHSCORE_SCRAPER_API_KEY = os.getenv("FLASHSCORE_SCRAPER_API_KEY")
FLASHSCORE_SCRAPER_TIMEOUT_S = float(os.getenv("FLASHSCORE_SCRAPER_TIMEOUT_S", "60"))

# Optional separate odds service — legacy escape hatch; v1 expects odds from OpenClaw
ODDS_SERVICE_URL = (os.getenv("ODDS_SERVICE_URL") or "").strip().rstrip("/") or None
ODDS_SERVICE_API_KEY = os.getenv("ODDS_SERVICE_API_KEY") or OPENCLAW_CONTEXT_API_KEY
ODDS_SERVICE_TIMEOUT_S = float(os.getenv("ODDS_SERVICE_TIMEOUT_S", "30"))

# Telegram bot long-running runtime
BOT_ANALYSIS_TIMEOUT_S = float(os.getenv("BOT_ANALYSIS_TIMEOUT_S", "120"))
BOT_HEALTH_PROBE_TIMEOUT_S = float(os.getenv("BOT_HEALTH_PROBE_TIMEOUT_S", "5"))

FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"

# Deprecated: prefer football_agent.league_registry.resolve_league_params / discovery_competition_codes
LEAGUE_IDS_FOOTBALL_DATA = build_league_ids_football_data()
LEAGUE_IDS_API_FOOTBALL = build_league_ids_api_football()
TOTAL_ROUNDS = build_total_rounds()
RELEGATION_SLOTS = build_relegation_slots()
EURO_SLOTS = dict(build_euro_slots())

CURRENT_SEASON = 2024

FOOTBALL_DATA_REQUEST_DELAY = 6.5  # free tier: 10 req/min → 6s между запросами
API_FOOTBALL_REQUEST_DELAY = 1.0
CACHE_TTL_SECONDS = 6 * 3600  # 6 часов

EXPRESS_MIN_PROBABILITY = 0.72
EXPRESS_MIN_ODDS = 1.15
EXPRESS_MAX_ODDS = 2.8
EXPRESS_MIN_LEG_ODDS = 1.22

# Best-market ranking: downweight very short prices (1X at ~1.07 etc.)
BEST_MARKET_MIN_USEFUL_ODDS = 1.28

# Runtime paths: DATA_DIR, CACHE_DIR, SNAPSHOTS_DIR, DEFAULT_DB_PATH (from football_agent.paths)
