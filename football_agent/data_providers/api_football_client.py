from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[2]))

import hashlib
import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List, Optional

import requests

from football_agent import config
from football_agent.domain.models import Odds

logger = logging.getLogger(__name__)


def _normalize_team_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"\b(fc|cf|afc)\b", "", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ApiFootballClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = config.API_FOOTBALL_BASE_URL
        self.cache_ttl_seconds = 12 * 3600

        self._cache_dir = Path(__file__).resolve().parents[1] / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update({"x-apisports-key": self.api_key or ""})

    def _cache_path(self, endpoint: str, params: Optional[dict]) -> Path:
        key = f"{endpoint}{str(params)}".encode("utf-8")
        filename = hashlib.md5(key).hexdigest() + ".json"
        return self._cache_dir / filename

    def _read_cache(self, path: Path) -> Optional[dict]:
        try:
            if not path.exists():
                return None
            age = time.time() - path.stat().st_mtime
            if age > self.cache_ttl_seconds:
                return None
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to read cache %s: %s", path, e)
            return None

    def _write_cache(self, path: Path, payload: dict) -> None:
        try:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            tmp.replace(path)
        except Exception as e:
            logger.warning("Failed to write cache %s: %s", path, e)

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        cache_path = self._cache_path(endpoint, params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        url = f"{self.base_url}{endpoint}"
        try:
            resp = self._session.get(url, params=params or {}, timeout=25)
        except Exception as e:
            logger.exception("Request failed %s params=%s: %s", endpoint, params, e)
            return {}

        if resp.status_code != 200:
            logger.warning("Non-200 from api-football %s: %s %s", endpoint, resp.status_code, resp.text[:300])
            return {}

        try:
            data = resp.json()
        except Exception as e:
            logger.warning("Failed to decode JSON %s: %s", endpoint, e)
            return {}

        if isinstance(data, dict):
            self._write_cache(cache_path, data)
            return data

        logger.warning("Unexpected response type %s: %s", endpoint, type(data))
        return {}

    def get_fixtures(self, league_id: int, date_str: str, season: int) -> List[dict]:
        data = self._get("/fixtures", params={"league": league_id, "date": date_str, "season": season})
        resp = data.get("response")
        return resp if isinstance(resp, list) else []

    def get_odds(self, fixture_id: int) -> Optional[Odds]:
        data = self._get("/odds", params={"fixture": fixture_id})
        resp = data.get("response")
        if not resp:
            return None

        entry = resp[0] if isinstance(resp, list) and resp else {}
        bookmakers = entry.get("bookmakers") or []

        # Find first bookmaker that provides needed markets (not necessarily all)
        for bm in bookmakers:
            bets = bm.get("bets") or []
            bets_by_name = {b.get("name"): b for b in bets if isinstance(b, dict)}

            o = Odds(fixture_id=fixture_id)
            found_any = False

            mw = bets_by_name.get("Match Winner")
            if mw:
                for v in (mw.get("values") or []):
                    val = str(v.get("value") or "")
                    odd = _to_float(v.get("odd"))
                    if odd is None:
                        continue
                    if val == "Home":
                        o.home_win = odd
                        found_any = True
                    elif val == "Draw":
                        o.draw = odd
                        found_any = True
                    elif val == "Away":
                        o.away_win = odd
                        found_any = True

            dc = bets_by_name.get("Double Chance")
            if dc:
                for v in (dc.get("values") or []):
                    val = str(v.get("value") or "")
                    odd = _to_float(v.get("odd"))
                    if odd is None:
                        continue
                    if val == "Home/Draw":
                        o.home_not_lose = odd
                        found_any = True
                    elif val == "Draw/Away":
                        o.away_not_lose = odd
                        found_any = True

            btts = bets_by_name.get("Both Teams Score")
            if btts:
                for v in (btts.get("values") or []):
                    val = str(v.get("value") or "")
                    odd = _to_float(v.get("odd"))
                    if odd is None:
                        continue
                    if val == "Yes":
                        o.btts_yes = odd
                        found_any = True

            if found_any:
                return o

        return Odds(fixture_id=fixture_id)

    def find_fixture_id(
        self,
        home_name: str,
        away_name: str,
        date_str: str,
        league_id: int,
        season: int,
    ) -> Optional[int]:
        fixtures = self.get_fixtures(league_id=league_id, date_str=date_str, season=season)
        if not fixtures:
            return None

        target_home = _normalize_team_name(home_name)
        target_away = _normalize_team_name(away_name)

        # Exact match first
        for f in fixtures:
            teams = f.get("teams") or {}
            home = (teams.get("home") or {}).get("name") or ""
            away = (teams.get("away") or {}).get("name") or ""
            if _normalize_team_name(str(home)) == target_home and _normalize_team_name(str(away)) == target_away:
                fx = f.get("fixture") or {}
                fid = fx.get("id")
                return int(fid) if fid is not None else None

        # Fuzzy match
        best_score = 0.0
        best_id: Optional[int] = None

        for f in fixtures:
            teams = f.get("teams") or {}
            home = _normalize_team_name(str((teams.get("home") or {}).get("name") or ""))
            away = _normalize_team_name(str((teams.get("away") or {}).get("name") or ""))

            home_score = SequenceMatcher(None, target_home, home).ratio()
            away_score = SequenceMatcher(None, target_away, away).ratio()
            score = (home_score + away_score) / 2.0

            if score > best_score:
                fx = f.get("fixture") or {}
                fid = fx.get("id")
                if fid is not None:
                    best_score = score
                    best_id = int(fid)

        return best_id if best_score >= 0.7 else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if not config.API_FOOTBALL_KEY:
        print("API_FOOTBALL_KEY is not set; skipping live test.")
    else:
        client = ApiFootballClient(api_key=config.API_FOOTBALL_KEY)
        fixtures = client.get_fixtures(league_id=config.LEAGUE_IDS_API_FOOTBALL["PL"], date_str="2024-04-25", season=2024)
        print(f"Fixtures: {len(fixtures)}")
