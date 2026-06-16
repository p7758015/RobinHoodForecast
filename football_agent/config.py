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
# Flashscore-first collector layer (match_meta / standings / form); default off
USE_COLLECTOR_LAYER = os.getenv("USE_COLLECTOR_LAYER", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Extract prematch odds embedded in Flashscore scraper raw (even when full collector layer is off).
USE_EMBEDDED_FLASHSCORE_ODDS = os.getenv("USE_EMBEDDED_FLASHSCORE_ODDS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# OpenClaw legacy match payload path (app_pipeline optional ingestion)
USE_OPENCLAW = os.getenv("USE_OPENCLAW", "false").strip().lower() in ("1", "true", "yes", "on")
# OpenClaw live enrichment sub-flags (Brave news phase — all default off)
USE_OPENCLAW_LIVE_CONTEXT = os.getenv("USE_OPENCLAW_LIVE_CONTEXT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
USE_OPENCLAW_NEWS = os.getenv("USE_OPENCLAW_NEWS", "false").strip().lower() in ("1", "true", "yes", "on")
USE_OPENCLAW_COACH_CONTEXT = os.getenv("USE_OPENCLAW_COACH_CONTEXT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
USE_BRAVE_NEWS_ENRICHMENT = os.getenv("USE_BRAVE_NEWS_ENRICHMENT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
OPENCLAW_FAIL_SOFT = os.getenv("OPENCLAW_FAIL_SOFT", "true").strip().lower() in ("1", "true", "yes", "on")
OPENCLAW_CAN_OVERRIDE_FACTUAL = os.getenv("OPENCLAW_CAN_OVERRIDE_FACTUAL", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
OPENCLAW_CAN_OVERRIDE_COACH_NAMES = os.getenv("OPENCLAW_CAN_OVERRIDE_COACH_NAMES", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Brave Search API (news/coach enrichment)
BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip() or None
BRAVE_SEARCH_BASE_URL = (os.getenv("BRAVE_SEARCH_BASE_URL") or "https://api.search.brave.com/res/v1/web/search").strip()
BRAVE_SEARCH_TIMEOUT_S = float(os.getenv("BRAVE_SEARCH_TIMEOUT_S", "15"))
BRAVE_SEARCH_MAX_RESULTS = int(os.getenv("BRAVE_SEARCH_MAX_RESULTS", "8"))
BRAVE_NEWS_LOOKBACK_HOURS = int(os.getenv("BRAVE_NEWS_LOOKBACK_HOURS", "72"))
BRAVE_NEWS_MAX_ARTICLES_PER_MATCH = int(os.getenv("BRAVE_NEWS_MAX_ARTICLES_PER_MATCH", "20"))
BRAVE_NEWS_INCLUDE_COACH_TERMS = os.getenv("BRAVE_NEWS_INCLUDE_COACH_TERMS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
BRAVE_NEWS_INCLUDE_INJURY_TERMS = os.getenv("BRAVE_NEWS_INCLUDE_INJURY_TERMS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
BRAVE_NEWS_INCLUDE_LINEUP_TERMS = os.getenv("BRAVE_NEWS_INCLUDE_LINEUP_TERMS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
BRAVE_COACH_HISTORY_LOOKBACK_DAYS = int(os.getenv("BRAVE_COACH_HISTORY_LOOKBACK_DAYS", "365"))
BRAVE_COACH_H2H_LOOKBACK_DAYS = int(os.getenv("BRAVE_COACH_H2H_LOOKBACK_DAYS", "730"))
BRAVE_COACH_QUOTES_LOOKBACK_DAYS = int(os.getenv("BRAVE_COACH_QUOTES_LOOKBACK_DAYS", "14"))
NEWS_REFRESH_MAX_AGE_MINUTES = int(os.getenv("NEWS_REFRESH_MAX_AGE_MINUTES", "45"))
NEWS_REFRESH_PRE_KICKOFF_WINDOW_MINUTES = int(os.getenv("NEWS_REFRESH_PRE_KICKOFF_WINDOW_MINUTES", "180"))
# OpenClaw bridge — stable JSON enrichment API (preferred over raw gateway when set)
OPENCLAW_BRIDGE_BASE_URL = (os.getenv("OPENCLAW_BRIDGE_BASE_URL") or "").strip().rstrip("/") or None
OPENCLAW_BRIDGE_MODE = (os.getenv("OPENCLAW_BRIDGE_MODE") or "prototype").strip().lower()
OPENCLAW_BRIDGE_PORT = int(os.getenv("OPENCLAW_BRIDGE_PORT", "8787"))
# Live-assisted bridge backend (OpenAI-compatible chat on OpenClaw gateway)
OPENCLAW_BRIDGE_API_KEY = os.getenv("OPENCLAW_BRIDGE_API_KEY") or os.getenv("OPENCLAW_API_KEY")
OPENCLAW_BRIDGE_MODEL = (os.getenv("OPENCLAW_BRIDGE_MODEL") or "gpt-4o-mini").strip()
OPENCLAW_BRIDGE_CHAT_PATH = (os.getenv("OPENCLAW_BRIDGE_CHAT_PATH") or "/v1/chat/completions").strip()
OPENCLAW_BRIDGE_LIVE_TIMEOUT_S = float(os.getenv("OPENCLAW_BRIDGE_LIVE_TIMEOUT_S", "30"))
# Upstream OpenClaw gateway for bridge live_assisted probe only (not football_agent direct client)
OPENCLAW_GATEWAY_URL = (os.getenv("OPENCLAW_GATEWAY_URL") or "").strip().rstrip("/") or None

# Primary unified enrichment backend (context + odds) — legacy direct gateway
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
# Optional Brave query normalization before Flashscore competition search (not fixture truth).
DISCOVERY_BRAVE_NORMALIZE = os.getenv("DISCOVERY_BRAVE_NORMALIZE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Pre-kickoff odds refresh (Refresh A — Flashscore collector only)
ODDS_REFRESH_MAX_AGE_MINUTES = int(os.getenv("ODDS_REFRESH_MAX_AGE_MINUTES", "60"))
ODDS_REFRESH_PRE_KICKOFF_WINDOW_MINUTES = int(os.getenv("ODDS_REFRESH_PRE_KICKOFF_WINDOW_MINUTES", "120"))

# Optional separate odds service — legacy escape hatch; v1 expects odds from OpenClaw
ODDS_SERVICE_URL = (os.getenv("ODDS_SERVICE_URL") or "").strip().rstrip("/") or None
ODDS_SERVICE_API_KEY = os.getenv("ODDS_SERVICE_API_KEY") or OPENCLAW_CONTEXT_API_KEY
ODDS_SERVICE_TIMEOUT_S = float(os.getenv("ODDS_SERVICE_TIMEOUT_S", "30"))

# Telegram bot long-running runtime
BOT_ANALYSIS_TIMEOUT_S = float(os.getenv("BOT_ANALYSIS_TIMEOUT_S", "120"))
TELEGRAM_LEAGUE_MAX_MATCHES = int(os.getenv("TELEGRAM_LEAGUE_MAX_MATCHES", "5"))
TELEGRAM_CLARIFICATION_TTL_S = float(os.getenv("TELEGRAM_CLARIFICATION_TTL_S", "600"))
# When list-by-date returns no wave-1 fixtures, try Flashscore competition discovery.
EVAL_POOL_DISCOVERY_FALLBACK = os.getenv("EVAL_POOL_DISCOVERY_FALLBACK", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
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
