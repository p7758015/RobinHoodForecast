# Telegram bot runtime (24/7 long polling)

Single-match analysis bot: Flashscore → OpenClaw context → odds → merge → score → persist.

## Quick start (local or server)

```bash
# from repository root
cd football_agent
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN + FLASHSCORE_SCRAPER_URL

# terminal 1 — Flashscore scraper (example)
# cd ../FlashscoreScraper && npm start

# terminal 2 — bot
python -m football_agent.bot.telegram_bot
```

The process runs until SIGINT/SIGTERM (Ctrl+C). Use **systemd**, **supervisor**, or **pm2** on a server to keep it alive — no code changes required.

## Required env

| Variable | Required | Notes |
|----------|----------|-------|
| `TELEGRAM_BOT_TOKEN` | **yes** | Bot will not start without it |

## Optional env (degraded mode if missing)

| Variable | Purpose |
|----------|---------|
| `FLASHSCORE_SCRAPER_URL` | Live match facts (`http://host:3000`) |
| `OPENCLAW_CONTEXT_BASE_URL` | Context enrichment |
| `ODDS_SERVICE_URL` | Bookmaker line enrichment |
| `*_API_KEY` | Per-service API keys where applicable |
| `BOT_ANALYSIS_TIMEOUT_S` | Max seconds per analysis (default `120`) |
| `BOT_HEALTH_PROBE_TIMEOUT_S` | `/health` probe timeout (default `5`) |

## Health / degraded mode

- Send `/health` in Telegram for live dependency status.
- Startup logs list configured deps and **degraded modes**:
  - `no_flashscore` — analysis unavailable
  - `no_openclaw_context` — facts-only + optional odds
  - `no_odds` — no book line in scoring output
  - `flashscore_unreachable` / `openclaw_unreachable` / `odds_unreachable` — URL set but `/health` probe failed

Missing OpenClaw or odds **does not block** bot startup.

## SQLite

Runtime DB: `football_agent/data/football_agent.db` (WAL mode, `busy_timeout=5s`).

## Logs

Structured stdout logs: inbound requests, analysis path, persistence, handler errors. Check server journal if using systemd.

## Server example (systemd sketch)

```ini
[Unit]
Description=RobinHood Forecast Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/RobinHoodForecast
EnvironmentFile=/opt/RobinHoodForecast/football_agent/.env
ExecStart=/usr/bin/python -m football_agent.bot.telegram_bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

(Docker / webhook not required for this stage.)
