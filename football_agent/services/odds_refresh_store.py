"""File-backed store for pre-kickoff odds refresh snapshots (no new DB table)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import Field

from football_agent.domain.models_v2 import V2IngestModel
from football_agent.odds.models import MatchOddsContext
from football_agent.paths import ODDS_REFRESH_DIR, ensure_runtime_dirs


class OddsRefreshRecord(V2IngestModel):
    match_key: str
    match_url: Optional[str] = None
    kickoff_utc: Optional[datetime] = None
    collected_at_utc: datetime
    refreshed_at_utc: datetime
    is_stale: bool = False
    source: str = "flashscore_collector"
    warnings: List[str] = Field(default_factory=list)
    odds_context: MatchOddsContext


class OddsRefreshStoreFile(V2IngestModel):
    current: Optional[OddsRefreshRecord] = None
    stale: List[OddsRefreshRecord] = Field(default_factory=list)


class OddsRefreshStore:
    def __init__(self, root: Path | None = None) -> None:
        ensure_runtime_dirs()
        self._root = root or ODDS_REFRESH_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def load(self, match_key: str) -> OddsRefreshStoreFile:
        path = self._path_for_key(match_key)
        if not path.is_file():
            return OddsRefreshStoreFile()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return OddsRefreshStoreFile.model_validate(raw)

    def save_current(
        self,
        record: OddsRefreshRecord,
    ) -> Path:
        store = self.load(record.match_key)
        if store.current is not None:
            prev = store.current.model_copy(update={"is_stale": True})
            store.stale.insert(0, prev)
            store.stale = store.stale[:5]
        store.current = record
        path = self._path_for_key(record.match_key)
        path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
        return path

    def _path_for_key(self, match_key: str) -> Path:
        digest = hashlib.sha256(match_key.encode("utf-8")).hexdigest()[:20]
        return self._root / f"{digest}.json"
