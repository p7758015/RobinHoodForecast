# Debug / CLI tools (Stage 4 live path)

League-match live debug only. Not wired to Telegram or `app_pipeline`.

## Prerequisites

1. **Flashscore scraper** at `FLASHSCORE_SCRAPER_URL` (default `http://localhost:3000`)
   - Health: `GET {base}/health` → `{"status":"ok"}`
   - Match: `GET {base}/v1/match?url=...` or `?match_id=...`

2. **OpenClaw bridge** (recommended enrichment) at `OPENCLAW_BRIDGE_BASE_URL` (default `http://localhost:8787`)
   - Start: `python -m football_agent.openclaw_bridge --port 8787 --mode prototype`
   - Health: `GET {base}/health` → `{"ok":true,"service":"openclaw_bridge"}`
   - Context: `GET {base}/v1/context?home=&away=&date=&competition_name=`
   - Odds: `GET {base}/v1/odds?home=&away=&date=`
   - See `football_agent/openclaw_bridge/README.md`

3. **OpenClaw legacy gateway** (optional upstream probe only) at `OPENCLAW_GATEWAY_URL` / `OPENCLAW_BASE_URL`
   - Canonical local/SSH-tunnel base: `http://localhost:18789`
   - `/health` is JSON; `/v1/context` returns HTML UI — **not** ingestible by football_agent directly
   - Use bridge (`OPENCLAW_BRIDGE_BASE_URL`) instead; set `OPENCLAW_BRIDGE_MODE=live_assisted` to probe gateway from bridge

Copy `football_agent/.env.example` → `football_agent/.env` and set URLs.

## Quick health check

```bash
python -m football_agent.debug.live_analysis_trace --check-services
python -m football_agent.debug.stage4_smoke --check-services --json
```

## Single match — `live_analysis_trace`

### Flashscore-only

```bash
python -m football_agent.debug.live_analysis_trace \
  --match-url "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc" \
  --skip-openclaw --no-persist --json
```

### Flashscore + OpenClaw (via bridge)

```bash
# Terminal 1 — prototype stubs (warning: bridge_prototype_mode)
python -m football_agent.openclaw_bridge --port 8787 --mode prototype

# Terminal 1 — live_assisted (chat backend on OpenClaw gateway :18789)
python -m football_agent.openclaw_bridge --port 8787 --mode live_assisted --gateway http://localhost:18789

# Terminal 2 — set OPENCLAW_BRIDGE_BASE_URL=http://localhost:8787 in .env
python -m football_agent.debug.live_analysis_trace \
  --match-url "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc" \
  --use-openclaw --json
```

Check JSON output for `extraction_warnings` (`live_backend_context_ok`, `partial_context`, `bridge_prototype_fallback`) and `report.coverage_score` / `sources.openclaw`.

### Degraded OpenClaw (fail-soft)

```bash
python -m football_agent.debug.live_analysis_trace \
  --match-url "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc" \
  --use-openclaw --openclaw-url "http://127.0.0.1:9" --json
```

### Persist + evaluation

```bash
python -m football_agent.debug.live_analysis_trace \
  --match-url "https://www.flashscore.com/match/football/avai-rPzY7fWt/ceara-p0JrJCV5/?mid=6FiXiHcc" \
  --skip-openclaw \
  --db-path football_agent/data/live_stage4.db \
  --evaluate --json
```

JSON includes: `sources`, `report.merge_missing_blocks`, link strategies, `scoring.best_market`, optional `completeness`, `run_id`.

## Brazil Serie B smoke batch — `stage4_smoke`

Canonical league matches (built-in URLs):

| Key | Match |
|-----|-------|
| `avai` | Avai vs Ceara |
| `goias` | Goias vs Novorizontino |
| `athletic` | Athletic Club vs Sport Recife |

```bash
# One command after scraper is up:
python -m football_agent.debug.stage4_smoke --check-services

python -m football_agent.debug.stage4_smoke --scenario flashscore-only --match avai --json

python -m football_agent.debug.stage4_smoke --scenario flashscore-openclaw --match all --json \
  --write-report football_agent/data/reports/stage4_smoke.json

python -m football_agent.debug.stage4_smoke --scenario openclaw-degraded --match avai

python -m football_agent.debug.stage4_smoke --scenario persist-eval --match avai \
  --db-path football_agent/data/live_stage4.db --write-report football_agent/data/reports/stage4_persist.json
```

Scenarios: `flashscore-only` | `flashscore-openclaw` | `openclaw-degraded` | `persist-eval`

## Fail-soft behavior

| Failure | Pipeline behavior |
|---------|-------------------|
| Scraper down / bad URL | Exit error; no partial analysis |
| OpenClaw down / timeout | WARNING; `merge_missing_blocks` includes `openclaw_context` |
| Odds missing | WARNING; `odds_context` missing; scorer continues |
| Empty OpenClaw env | `enrichment_not_configured`; flashscore-only path |

## Other debug CLIs

- `flashscore_trace` — Flashscore facts only / fixture export
- `merged_scoring_trace` — offline fixtures → scorer
- `offline_evaluation_trace` — persisted runs evaluation

## Eval wave — `eval_wave_runner`

Operational preset `june18_21_first_batch` (and custom manifests under `data/eval_waves/`).

**Storage:** SQLite `football_agent/data/football_agent.db` — tables `analysis_runs_v2`, `analysis_predictions_v2`, `analysis_snapshots_v2`, `match_results` (see `eval_pool/wave_predictions.py`).

### View predictions (read-only)

```bash
cd football_agent

# Terminal table — all scored runs in wave date/pool scope
python -m football_agent.debug.eval_wave_runner list-wave-predictions --preset june18_21_first_batch

# JSON export
python -m football_agent.debug.eval_wave_runner list-wave-predictions --preset june18_21_first_batch --json

# One run by UUID
python -m football_agent.debug.eval_wave_runner show-run --run-id <run_id>
```

Columns: `date`, `pool_key`, `home`, `away`, `p_h`/`p_d`/`p_a` (model 1X2), `best` market, `p*`, book `odds`, `conf`, `score`, settle `stl`, truncated `run_id`.

### Report / full cycle

| Command | Writes DB? | Purpose |
|---------|------------|---------|
| `accumulate-wave` | yes (runs) | Discover fixtures → live pipeline → persist |
| `update-results` | yes (`match_results`) | Finished scores from Flashscore |
| `settle-wave` | no | Hit-rate stats (join predictions + results) |
| `report-wave` | no | Coverage + calibration + **predictions markdown** artifact |
| `full-wave` | yes + report | accumulate → update-results → settle stats → report |
| `cleanup-wave` | yes (delete) | Remove wave runs (`--apply`; dry-run default) |
| `list-wave-predictions` | no | Human-readable prediction list |
| `show-run` | no | Single run detail |

```bash
# Metrics + artifacts (JSON + markdown + predictions table) — no re-accumulate
python -m football_agent.debug.eval_wave_runner report-wave --preset june18_21_first_batch

# Full operational cycle (re-runs accumulate + fetches results)
python -m football_agent.debug.eval_wave_runner full-wave --preset june18_21_first_batch
```

Artifacts: `football_agent/data/eval_wave_reports/<wave>_<timestamp>.{json,md,_predictions.md}`.

