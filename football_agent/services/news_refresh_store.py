"""File-backed store for pre-kickoff news enrichment snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import Field

from football_agent.domain.models_v2 import V2IngestModel
from football_agent.news_context.models import MatchNewsContext
from football_agent.paths import NEWS_REFRESH_DIR, ensure_runtime_dirs


class NewsRefreshRecord(V2IngestModel):
    match_key: str
    match_url: Optional[str] = None
    kickoff_utc: Optional[datetime] = None
    collected_at_utc: datetime
    refreshed_at_utc: datetime
    is_stale: bool = False
    source: str = "brave_search"
    warnings: List[str] = Field(default_factory=list)
    news_context: MatchNewsContext


class NewsRefreshStoreFile(V2IngestModel):
    current: Optional[NewsRefreshRecord] = None
    stale: List[NewsRefreshRecord] = Field(default_factory=list)


class NewsRefreshStore:
    def __init__(self, root: Path | None = None) -> None:
        ensure_runtime_dirs()
        self._root = root or NEWS_REFRESH_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def load(self, match_key: str) -> NewsRefreshStoreFile:
        path = self._path_for_key(match_key)
        if not path.is_file():
            return NewsRefreshStoreFile()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return NewsRefreshStoreFile.model_validate(raw)

    def save_current(self, record: NewsRefreshRecord) -> Path:
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
