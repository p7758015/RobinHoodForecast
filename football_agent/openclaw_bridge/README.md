# OpenClaw Bridge



Stable JSON enrichment API between **football_agent** and the real OpenClaw gateway.



The OpenClaw gateway on `:18789` exposes `/health` as JSON but `/v1/context` returns HTML UI — not ingestible by `HttpOpenClawContextAdapter`. This bridge normalizes match-centric input into the contract expected by `openclaw_context` and `odds` layers.



## Endpoints



| Path | Method | Purpose |

|------|--------|---------|

| `/health` | GET | `{"ok":true,"status":"live","service":"openclaw_bridge"}` |

| `/v1/context` | GET, POST | OpenClaw context raw JSON (maps to `OpenClawMatchContext`) |

| `/v1/odds` | GET, POST | Odds raw JSON (maps to `MatchOddsContext`) |



### Query params (GET `/v1/context` and `/v1/odds`)



- `home` / `home_team` (required)

- `away` / `away_team` (required)

- `competition_name` / `competition`

- `competition_code`

- `date` (YYYY-MM-DD)

- `kickoff_utc`

- `country`

- `match_id`

- `url` / `match_url`

- optional hints: `home_form`, `away_form`, `standings`



## Modes



| Mode | Behaviour |

|------|-----------|

| `prototype` | Contract-correct partial JSON (LOW confidence stubs). Warning: `bridge_prototype_mode`. |

| `live_assisted` | 1) Probe gateway `/v1/context` + `/v1/odds` for JSON passthrough. 2) Call OpenAI-compatible chat (`/v1/chat/completions`, fallback `/v1/responses`) on `OPENCLAW_GATEWAY_URL`. 3) Merge live blocks; fill gaps from prototype stubs. Warnings: `live_backend_context_ok`, `live_backend_odds_ok`, `partial_context`, `partial_odds`, `bridge_prototype_fallback`, `backend_unavailable`, `backend_invalid_payload`. |



Warnings are returned in `extraction_warnings` (never free text to pipeline).



## Run locally



```bash

# Terminal 1 — prototype (stubs only)

python -m football_agent.openclaw_bridge --port 8787 --mode prototype



# Terminal 1 — live_assisted (chat backend on OpenClaw gateway)

python -m football_agent.openclaw_bridge --port 8787 --mode live_assisted --gateway http://localhost:18789



# Terminal 2 — football_agent via bridge

python -m football_agent.debug.live_analysis_trace \

  --match-url "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc" \

  --use-openclaw --json

```



With `OPENCLAW_BRIDGE_BASE_URL` set, enrichment routing uses the bridge automatically.



## Env



```env

# football_agent enrichment target

OPENCLAW_BRIDGE_BASE_URL=http://localhost:8787

OPENCLAW_BRIDGE_MODE=live_assisted

OPENCLAW_BRIDGE_PORT=8787



# Live backend (OpenAI-compatible chat on OpenClaw gateway)

OPENCLAW_BRIDGE_API_KEY=

OPENCLAW_BRIDGE_MODEL=gpt-4o-mini

OPENCLAW_BRIDGE_CHAT_PATH=/v1/chat/completions

OPENCLAW_BRIDGE_LIVE_TIMEOUT_S=30



# Upstream gateway (probe + chat)

OPENCLAW_GATEWAY_URL=http://localhost:18789

```



Priority: `OPENCLAW_BRIDGE_BASE_URL` > `OPENCLAW_BASE_URL` > `OPENCLAW_CONTEXT_BASE_URL` for enrichment HTTP client.



## Example responses



### Context (`GET /v1/context?home=Avai&away=Ceara&date=2026-06-10`)



```json

{

  "query_home_team": "Avai",

  "query_away_team": "Ceara",

  "motivation_narrative": { "...": "..." },

  "squad_context": { "...": "..." },

  "coach_context": { "...": "..." },

  "news": { "match_news_items": [] },

  "fatigue_schedule_context": { "...": "..." },

  "backend_name": "openclaw_bridge",

  "bridge_mode": "live_assisted",

  "extraction_warnings": ["live_backend_context_ok"]

}

```



### Odds (`GET /v1/odds?home=Avai&away=Ceara`)



```json

{

  "fixture_id": "bridge-6FiXiHcc",

  "home_team": "Avai",

  "away_team": "Ceara",

  "markets": {

    "home_win": {"odds_value": 2.35, "bookmaker_name": "openclaw_bridge", "confidence": "MEDIUM"},

    "double_chance_1x": {"odds_value": 1.38, "bookmaker_name": "openclaw_bridge", "confidence": "MEDIUM"}

  },

  "extraction_warnings": ["live_backend_odds_ok"]

}

```



## Architecture



```

football_agent → OPENCLAW_BRIDGE_BASE_URL (/v1/context, /v1/odds)

                      ↓

              BridgeEnricher

                 ├─ prototype mode → stubs

                 └─ live_assisted

                      ├─ GET gateway /v1/* (JSON passthrough if available)

                      └─ POST gateway /v1/chat/completions → structured JSON

                           → normalizer → bridge contract

```



Legacy direct gateway URLs remain for backward compatibility when bridge is unset.


