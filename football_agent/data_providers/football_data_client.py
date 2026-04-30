from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[2]))

import hashlib
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from football_agent import config
from football_agent.domain.models import CoachMatch, Match, MatchResult, StandingEntry, Team

logger = logging.getLogger(__name__)


def _parse_utc_datetime(value: str) -> datetime:
    # football-data.org returns ISO with "Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class FootballDataClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = config.FOOTBALL_DATA_BASE_URL
        self.request_delay = config.FOOTBALL_DATA_REQUEST_DELAY
        self.cache_ttl_seconds = config.CACHE_TTL_SECONDS

        self._cache_dir = Path(__file__).resolve().parents[1] / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update({"X-Auth-Token": self.api_key or ""})

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
            resp = self._session.get(url, params=params or {}, timeout=20)
        except Exception as e:
            logger.exception("Request failed %s params=%s: %s", endpoint, params, e)
            return {}
        finally:
            time.sleep(self.request_delay)

        if resp.status_code != 200:
            logger.warning("Non-200 from football-data %s: %s %s", endpoint, resp.status_code, resp.text[:300])
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

    def get_standings(self, competition_code: str) -> List[StandingEntry]:
        data = self._get(f"/competitions/{competition_code}/standings")
        standings = data.get("standings") or []
        total_block = next((s for s in standings if s.get("type") == "TOTAL"), None)
        table = (total_block or {}).get("table") or []

        result: List[StandingEntry] = []
        for row in table:
            team_data = row.get("team") or {}
            team = Team(
                id=_safe_int(team_data.get("id")),
                name=str(team_data.get("name") or ""),
                short_name=str(team_data.get("shortName") or team_data.get("name") or ""),
            )
            result.append(
                StandingEntry(
                    team=team,
                    position=_safe_int(row.get("position")),
                    points=_safe_int(row.get("points")),
                    played_games=_safe_int(row.get("playedGames")),
                    won=_safe_int(row.get("won")),
                    draw=_safe_int(row.get("draw")),
                    lost=_safe_int(row.get("lost")),
                    goals_for=_safe_int(row.get("goalsFor")),
                    goals_against=_safe_int(row.get("goalsAgainst")),
                    goal_difference=_safe_int(row.get("goalDifference")),
                    form=str(row.get("form") or ""),
                )
            )
        return result

    def _parse_match(self, m: dict, competition_code: str) -> Match:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        ft = ((m.get("score") or {}).get("fullTime") or {})

        home_team = Team(
            id=_safe_int(home.get("id")),
            name=str(home.get("name") or ""),
            short_name=str(home.get("shortName") or home.get("name") or ""),
        )
        away_team = Team(
            id=_safe_int(away.get("id")),
            name=str(away.get("name") or ""),
            short_name=str(away.get("shortName") or away.get("name") or ""),
        )

        return Match(
            id=_safe_int(m.get("id")),
            competition_code=competition_code,
            home_team=home_team,
            away_team=away_team,
            utc_date=_parse_utc_datetime(str(m.get("utcDate"))),
            status=str(m.get("status") or ""),
            home_score=ft.get("home"),
            away_score=ft.get("away"),
            matchday=_safe_int(m.get("matchday")),
        )

    def get_matches_by_date(self, date_str: str) -> List[Match]:
        matches: List[Match] = []
        for code in config.LEAGUE_IDS_FOOTBALL_DATA.keys():
            data = self._get(
                f"/competitions/{code}/matches",
                params={"dateFrom": date_str, "dateTo": date_str, "status": "SCHEDULED"},
            )
            for m in (data.get("matches") or []):
                matches.append(self._parse_match(m, competition_code=code))
        return matches

    def get_finished_matches_by_date(self, date_str: str) -> List[Match]:
        matches: List[Match] = []
        for code in config.LEAGUE_IDS_FOOTBALL_DATA.keys():
            data = self._get(
                f"/competitions/{code}/matches",
                params={"dateFrom": date_str, "dateTo": date_str, "status": "FINISHED"},
            )
            for m in (data.get("matches") or []):
                matches.append(self._parse_match(m, competition_code=code))
        return matches

    def get_team_matches_season(self, team_id: int, season: int) -> List[MatchResult]:
        data = self._get(f"/teams/{team_id}/matches", params={"status": "FINISHED", "season": season})
        result: List[MatchResult] = []

        for m in (data.get("matches") or []):
            home = m.get("homeTeam") or {}
            away = m.get("awayTeam") or {}
            ft = ((m.get("score") or {}).get("fullTime") or {})
            hs, as_ = ft.get("home"), ft.get("away")
            if hs is None or as_ is None:
                continue

            is_home = _safe_int(home.get("id")) == team_id
            goals_for = int(hs) if is_home else int(as_)
            goals_against = int(as_) if is_home else int(hs)

            if goals_for == goals_against:
                res = "D"
            elif goals_for > goals_against:
                res = "W"
            else:
                res = "L"

            result.append(
                MatchResult(
                    match_id=_safe_int(m.get("id")),
                    date=_parse_utc_datetime(str(m.get("utcDate"))).date(),
                    is_home=is_home,
                    goals_for=goals_for,
                    goals_against=goals_against,
                    result=res,
                    coach_id=None,
                )
            )

        return result

    def get_team_coach(self, team_id: int) -> Tuple[Optional[int], Optional[str], Optional[date]]:
        data = self._get(f"/teams/{team_id}")
        coach = (data.get("coach") or {}) if isinstance(data, dict) else {}
        if not coach:
            return (None, None, None)

        coach_id = coach.get("id")
        coach_name = coach.get("name")
        start = ((coach.get("contract") or {}).get("start")) if isinstance(coach.get("contract"), dict) else None

        coach_start_date: Optional[date] = None
        if start:
            try:
                coach_start_date = date.fromisoformat(str(start))
            except ValueError:
                coach_start_date = None

        return (
            int(coach_id) if coach_id is not None else None,
            str(coach_name) if coach_name is not None else None,
            coach_start_date,
        )

    def get_coach_matches(self, person_id: int) -> List[CoachMatch]:
        data = self._get(f"/persons/{person_id}/matches", params={"role": "MANAGER"})
        result: List[CoachMatch] = []

        for m in (data.get("matches") or []):
            managed = m.get("managedTeam") or {}
            team_id = _safe_int(managed.get("id"))
            if not team_id:
                continue

            home = m.get("homeTeam") or {}
            away = m.get("awayTeam") or {}
            home_id, away_id = _safe_int(home.get("id")), _safe_int(away.get("id"))
            is_home = team_id == home_id
            opponent_id = away_id if is_home else home_id

            ft = ((m.get("score") or {}).get("fullTime") or {})
            hs, as_ = ft.get("home"), ft.get("away")
            if hs is None or as_ is None:
                continue

            goals_for = int(hs) if is_home else int(as_)
            goals_against = int(as_) if is_home else int(hs)

            if goals_for == goals_against:
                res = "D"
            elif goals_for > goals_against:
                res = "W"
            else:
                res = "L"

            result.append(
                CoachMatch(
                    match_id=_safe_int(m.get("id")),
                    team_id=team_id,
                    opponent_id=opponent_id,
                    result=res,
                    date=_parse_utc_datetime(str(m.get("utcDate"))).date(),
                )
            )

        return result

    def get_h2h_matches(self, team1_id: int, team2_id: int, season: int) -> List[MatchResult]:
        data = self._get(f"/teams/{team1_id}/matches", params={"status": "FINISHED", "season": season})
        result: List[MatchResult] = []

        for m in (data.get("matches") or []):
            home = m.get("homeTeam") or {}
            away = m.get("awayTeam") or {}
            home_id, away_id = _safe_int(home.get("id")), _safe_int(away.get("id"))
            if team2_id not in (home_id, away_id):
                continue

            ft = ((m.get("score") or {}).get("fullTime") or {})
            hs, as_ = ft.get("home"), ft.get("away")
            if hs is None or as_ is None:
                continue

            is_home = home_id == team1_id
            goals_for = int(hs) if is_home else int(as_)
            goals_against = int(as_) if is_home else int(hs)

            if goals_for == goals_against:
                res = "D"
            elif goals_for > goals_against:
                res = "W"
            else:
                res = "L"

            result.append(
                MatchResult(
                    match_id=_safe_int(m.get("id")),
                    date=_parse_utc_datetime(str(m.get("utcDate"))).date(),
                    is_home=is_home,
                    goals_for=goals_for,
                    goals_against=goals_against,
                    result=res,
                    coach_id=None,
                )
            )

        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if not config.FOOTBALL_DATA_API_KEY:
        print("FOOTBALL_DATA_API_KEY is not set; skipping live test.")
    else:
        client = FootballDataClient(api_key=config.FOOTBALL_DATA_API_KEY)
        standings = client.get_standings("PL")
        print(f"Standings entries: {len(standings)}")
