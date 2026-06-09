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
from football_agent.data_providers.odds_utils import count_odds_fields, merge_odds, seasons_to_try
from football_agent.domain.models import Odds
from football_agent.paths import CACHE_DIR, ensure_runtime_dirs

logger = logging.getLogger(__name__)


def _normalize_team_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"\b(fc|cf|afc)\b", "", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_bet_value(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _pick_odd_from_values(values: list, *accepted: str) -> Optional[float]:
    accepted_set = {_norm_bet_value(a) for a in accepted}
    for v in values or []:
        if not isinstance(v, dict):
            continue
        if _norm_bet_value(str(v.get("value") or "")) in accepted_set:
            return _to_float(v.get("odd"))
    return None


def _bet_by_names(bets_by_name: dict, *names: str) -> Optional[dict]:
    for name in names:
        bet = bets_by_name.get(name)
        if bet:
            return bet
    return None


def _parse_match_winner(bets_by_name: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    mw = _bet_by_names(bets_by_name, "Match Winner", "1x2", "Full Time Result")
    if not mw:
        return None, None, None
    home = draw = away = None
    for v in mw.get("values") or []:
        if not isinstance(v, dict):
            continue
        val = _norm_bet_value(str(v.get("value") or ""))
        odd = _to_float(v.get("odd"))
        if odd is None:
            continue
        if val in ("home", "1", "team 1"):
            home = odd
        elif val in ("draw", "x"):
            draw = odd
        elif val in ("away", "2", "team 2"):
            away = odd
    return home, draw, away


def _parse_double_chance(bets_by_name: dict) -> tuple[Optional[float], Optional[float]]:
    dc = _bet_by_names(bets_by_name, "Double Chance")
    if not dc:
        return None, None
    home_dc = away_dc = None
    for v in dc.get("values") or []:
        if not isinstance(v, dict):
            continue
        val = _norm_bet_value(str(v.get("value") or ""))
        odd = _to_float(v.get("odd"))
        if odd is None:
            continue
        if val in ("home/draw", "1x", "1/x", "home or draw", "home & draw"):
            home_dc = odd
        elif val in ("draw/away", "x2", "x/2", "away or draw", "away & draw", "draw/away"):
            away_dc = odd
    return home_dc, away_dc


def _parse_btts_yes(bets_by_name: dict) -> Optional[float]:
    btts = _bet_by_names(
        bets_by_name,
        "Both Teams Score",
        "Both Teams To Score",
        "Both teams to score",
        "BTTS",
    )
    if not btts:
        return None
    return _pick_odd_from_values(btts.get("values") or [], "yes")


def _parse_bookmaker_bets(bets: list, fixture_id: int) -> Odds:
    bets_by_name = {b.get("name"): b for b in bets if isinstance(b, dict) and b.get("name")}
    o = Odds(fixture_id=fixture_id)
    hw, dr, aw = _parse_match_winner(bets_by_name)
    if hw is not None:
        o.home_win = hw
    if dr is not None:
        o.draw = dr
    if aw is not None:
        o.away_win = aw
    hnl, anl = _parse_double_chance(bets_by_name)
    if hnl is not None:
        o.home_not_lose = hnl
    if anl is not None:
        o.away_not_lose = anl
    btts = _parse_btts_yes(bets_by_name)
    if btts is not None:
        o.btts_yes = btts
    over15 = _parse_over_15(bets_by_name)
    if over15 is not None:
        o.over_15 = over15
    hts = _parse_team_to_score_yes(bets_by_name, "home")
    if hts is not None:
        o.home_team_to_score = hts
    ats = _parse_team_to_score_yes(bets_by_name, "away")
    if ats is not None:
        o.away_team_to_score = ats
    return o


def _parse_over_15(bets_by_name: dict) -> Optional[float]:
    for bet_name in ("Goals Over/Under", "Total Goals", "Goal Line"):
        bet = bets_by_name.get(bet_name)
        if not bet:
            continue
        odd = _pick_odd_from_values(bet.get("values") or [], "over 1.5", "over 1.5 goals")
        if odd is not None:
            return odd
    return None


def _parse_team_to_score_yes(bets_by_name: dict, side: str) -> Optional[float]:
    if side == "home":
        direct_names = ("Home Team Score a Goal", "Home Team To Score", "Home To Score")
        team_val = ("home", "home team", "1", "team 1")
    else:
        direct_names = ("Away Team Score a Goal", "Away Team To Score", "Away To Score")
        team_val = ("away", "away team", "2", "team 2")

    for name in direct_names:
        bet = bets_by_name.get(name)
        if bet:
            odd = _pick_odd_from_values(bet.get("values") or [], "yes")
            if odd is not None:
                return odd

    tts = bets_by_name.get("Team To Score")
    if tts:
        for v in tts.get("values") or []:
            if not isinstance(v, dict):
                continue
            val = _norm_bet_value(str(v.get("value") or ""))
            if val in team_val or (side == "home" and val == "home") or (side == "away" and val == "away"):
                odd = _to_float(v.get("odd"))
                if odd is not None:
                    return odd
    return None


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

        ensure_runtime_dirs()
        self._cache_dir = CACHE_DIR

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

    def get_fixtures(
        self,
        league_id: int,
        date_str: str,
        season: Optional[int] = None,
    ) -> List[dict]:
        params: dict = {"league": league_id, "date": date_str}
        if season is not None:
            params["season"] = season
        data = self._get("/fixtures", params=params)
        resp = data.get("response")
        return resp if isinstance(resp, list) else []

    def get_odds(self, fixture_id: int) -> Optional[Odds]:
        data = self._get("/odds", params={"fixture": fixture_id})
        resp = data.get("response")
        if not resp:
            return None

        entry = resp[0] if isinstance(resp, list) and resp else {}
        bookmakers = entry.get("bookmakers") or []

        merged = Odds(fixture_id=fixture_id)
        for bm in bookmakers:
            bets = bm.get("bets") or []
            partial = _parse_bookmaker_bets(bets, fixture_id)
            if count_odds_fields(partial) > 0:
                merge_odds(merged, partial)

        if count_odds_fields(merged) > 0:
            logger.debug(
                "Odds fixture %s: %s markets from %s bookmakers",
                fixture_id,
                count_odds_fields(merged),
                len(bookmakers),
            )
            return merged

        return Odds(fixture_id=fixture_id)

    def find_fixture_id(
        self,
        home_name: str,
        away_name: str,
        date_str: str,
        league_id: int,
        season: int,
        *,
        seasons: Optional[List[int]] = None,
    ) -> Optional[int]:
        for try_season in seasons_to_try(*(seasons or []), season):
            fid = self._find_fixture_id_in_fixtures(
                home_name, away_name, self.get_fixtures(league_id, date_str, try_season)
            )
            if fid is not None:
                return fid

        # Date-only fallback (no season filter)
        fid = self._find_fixture_id_in_fixtures(
            home_name, away_name, self.get_fixtures(league_id, date_str, None)
        )
        return fid

    @staticmethod
    def _find_fixture_id_in_fixtures(
        home_name: str,
        away_name: str,
        fixtures: List[dict],
    ) -> Optional[int]:
        if not fixtures:
            return None

        target_home = _normalize_team_name(home_name)
        target_away = _normalize_team_name(away_name)

        for f in fixtures:
            teams = f.get("teams") or {}
            home = (teams.get("home") or {}).get("name") or ""
            away = (teams.get("away") or {}).get("name") or ""
            if _normalize_team_name(str(home)) == target_home and _normalize_team_name(str(away)) == target_away:
                fx = f.get("fixture") or {}
                fid = fx.get("id")
                return int(fid) if fid is not None else None

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
