"""Short-lived file cache for Brave MatchNewsContext (stability between runs)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from football_agent import config
from football_agent.news_context.models import MatchNewsContext
from football_agent.paths import NEWS_REFRESH_DIR, ensure_runtime_dirs


class BraveNewsCache:
    def __init__(self, root: Path | None = None) -> None:
        ensure_runtime_dirs()
        self._root = (root or NEWS_REFRESH_DIR) / "brave_cache"
        self._root.mkdir(parents=True, exist_ok=True)

    def get(self, match_key: str, *, max_age_minutes: Optional[int] = None) -> Optional[MatchNewsContext]:
        if not config.BRAVE_NEWS_CACHE_ENABLED:
            return None
        path = self._path(match_key)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            collected = raw.get("collected_at_utc")
            if collected:
                dt = datetime.fromisoformat(str(collected).replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - dt
                limit = max_age_minutes or config.BRAVE_NEWS_CACHE_MAX_AGE_MINUTES
                if age > timedelta(minutes=max(1, limit)):
                    return None
            return MatchNewsContext.model_validate(raw.get("news_context") or raw)
        except (OSError, ValueError, TypeError):
            return None

    def put(self, match_key: str, news: MatchNewsContext) -> None:
        if not config.BRAVE_NEWS_CACHE_ENABLED:
            return
        payload = {
            "match_key": match_key,
            "collected_at_utc": (news.collected_at_utc or datetime.now(timezone.utc)).isoformat(),
            "news_context": news.model_dump(mode="json"),
        }
        self._path(match_key).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path(self, match_key: str) -> Path:
        digest = hashlib.sha256(match_key.encode("utf-8")).hexdigest()[:24]
        return self._root / f"{digest}.json"
