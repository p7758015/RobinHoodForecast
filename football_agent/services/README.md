# services (v2)

- `LeagueAnalysisServiceV2` — league pipeline: `FootballDataClient` scheduled matches → `MatchSnapshotBuilder` → `LeagueScorerV2` → `MatchPredictionResultV2`

Legacy orchestration remains in `engine/match_analyzer.py` and `main.py`.
