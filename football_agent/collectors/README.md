# Collector layer (Phase A / B.1)

Flashscore-first match data collection with block-level validation, trace, and fail-soft orchestration.

## Scope (current increment)

- Blocks: `match_meta`, `teams` (standings), `form`
- Source: Flashscore scraper only
- Feature flag: `USE_COLLECTOR_LAYER=true`

## Modules

| Module | Role |
|--------|------|
| `contracts.py` | Pydantic models |
| `trace.py` | Collection trace builder |
| `confidence.py` | Per-block confidence |
| `orchestrator.py` | Runs 3 block collectors |
| `flashscore/client.py` | HTTP client wrapper |
| `flashscore/fixture_collector.py` | match_meta validation |
| `flashscore/standings_collector.py` | table inputs |
| `flashscore/form_collector.py` | recent results |

## Debug artifacts

Raw JSON: `football_agent/data/snapshots/collector_raw/{match_key}/{block}/`
