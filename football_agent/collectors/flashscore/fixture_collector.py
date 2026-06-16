"""Fixture / match_meta block collector."""

from __future__ import annotations

from typing import Any, Dict, List

from football_agent.collectors.confidence import match_meta_confidence
from football_agent.collectors.contracts import (
    BlockCollectionResult,
    MatchRef,
    SourceAttempt,
    utc_now,
)
from football_agent.collectors.flashscore.validation import (
    is_valid_competition_name,
    is_valid_team_name,
    normalize_team_name,
)
from football_agent.flashscore.service import FlashscoreIngestionService


class FixtureMatchCollector:
    """Collect and validate match_meta from enriched Flashscore raw JSON."""

    BLOCK = "match_meta"
    SOURCE = "flashscore"

    def collect(self, raw: Dict[str, Any], ref: MatchRef) -> BlockCollectionResult:
        started = utc_now()
        warnings: List[str] = []

        home = normalize_team_name(
            raw.get("home_team_name") or raw.get("home") or ref.home_team,
        )
        away = normalize_team_name(
            raw.get("away_team_name") or raw.get("away") or ref.away_team,
        )
        competition = str(
            raw.get("competition_name") or raw.get("league_name") or raw.get("competition") or "",
        ).strip()
        competition_code = str(raw.get("competition_code") or ref.competition_code or "").strip() or None

        teams_valid = is_valid_team_name(home) and is_valid_team_name(away)
        comp_valid, comp_warnings = is_valid_competition_name(competition)
        warnings.extend(comp_warnings)

        kickoff_raw = raw.get("kickoff_utc")
        kickoff_dt = FlashscoreIngestionService._parse_datetime(kickoff_raw)  # noqa: SLF001
        kickoff_present = kickoff_dt is not None
        if not kickoff_present and kickoff_raw:
            warnings.append("match_meta_kickoff_unparseable")

        venue = str(raw.get("venue_name") or raw.get("venue") or "").strip() or None
        stage = str(raw.get("stage") or "").strip() or None
        round_val = raw.get("round") or raw.get("matchday_number")
        round_present = round_val is not None and str(round_val).strip() != ""

        confidence, status, conf_warnings = match_meta_confidence(
            home_team=home,
            away_team=away,
            competition_name=competition,
            kickoff_present=kickoff_present,
            venue_present=bool(venue),
            round_present=round_present,
            competition_valid=comp_valid,
            teams_valid=teams_valid,
        )
        warnings.extend(conf_warnings)

        payload: Dict[str, Any] = {
            "match_id": str(raw.get("match_id") or raw.get("id") or ref.match_id or ""),
            "home_team": home,
            "away_team": away,
            "competition_name": competition,
            "competition_code": competition_code,
            "competition_country": raw.get("competition_country"),
            "kickoff_utc": kickoff_dt.isoformat() if kickoff_dt else None,
            "venue_name": venue,
            "stage": stage,
            "round": str(round_val) if round_val is not None else None,
            "source_url": raw.get("source_url") or raw.get("url") or ref.match_url,
            "status": raw.get("status"),
        }

        finished = utc_now()
        attempt = SourceAttempt(
            block=self.BLOCK,
            source=self.SOURCE,
            started_at_utc=started,
            finished_at_utc=finished,
            status="failed" if status == "failed" else status,  # type: ignore[arg-type]
            warnings=list(warnings),
            raw_ref=raw.get("_collector_raw_ref"),
            duration_ms=int((finished - started).total_seconds() * 1000),
        )

        parse_report = {
            "block": self.BLOCK,
            "fields_present": [k for k, v in payload.items() if v],
            "fields_missing": [k for k, v in payload.items() if not v and k not in ("competition_code", "venue_name")],
            "validation": {
                "teams_valid": teams_valid,
                "competition_valid": comp_valid,
            },
            "confidence_trace": {"result": confidence, "status": status},
        }
        raw["_fixture_parse_report"] = parse_report

        return BlockCollectionResult(
            block=self.BLOCK,
            status=status,
            confidence=confidence,
            source=self.SOURCE,
            collected_at_utc=finished,
            payload=payload,
            warnings=warnings,
            attempts=[attempt],
            raw_ref=raw.get("_collector_raw_ref"),
        )
