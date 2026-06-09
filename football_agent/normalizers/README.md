# normalizers

Преобразование v1 API / legacy models → v2 contracts.

- `match_snapshot_builder.py` — `MatchSnapshotBuilder` → `MatchAnalysisSnapshotV2`
- `data_providers/*_client.py` — по-прежнему низкоуровневый fetch + v1 parse
