"""
v1 / v2 pipeline dispatch for CLI (main.py). Keeps orchestration out of main entrypoint.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

from football_agent.config import OPENCLAW_BASE_URL, USE_OPENCLAW, USE_V2_PIPELINE
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.domain.models_v2 import ExpressBetV2, MatchPredictionResultV2
from football_agent.engine.express_builder import build_express
from football_agent.engine.match_analyzer import analyze_matches_for_date, analyze_single_match
from football_agent.express.express_builder_v2 import ExpressBuilderV2
from football_agent.llm.agent import format_response, format_v2_or_llm, parse_user_request
from football_agent.output.v2_user_output import (
    build_match_payload_from_result,
    format_v2_all_matches_text,
    format_v2_express_text,
    format_v2_single_match_text,
)
from football_agent.services.league_analysis_service_v2 import LeagueAnalysisServiceV2
from football_agent.openclaw.service import OpenClawLeagueAnalysisService
from football_agent.storage.database import Database

logger = logging.getLogger(__name__)


@runtime_checkable
class LeagueV2IngestProtocol(Protocol):
    """Minimal surface used by `_run_v2` (legacy + OpenClaw)."""

    def analyze_matches_for_date(self, date_str: str, competition_code: Optional[str] = None) -> List[Any]: ...

    def analyze_match_by_teams(
        self,
        home_team_name: str,
        away_team_name: str,
        date_str: str,
        competition_code: Optional[str] = None,
    ) -> Tuple[Any, Optional[str]]: ...


def _openclaw_ingestion_enabled() -> bool:
    if not USE_OPENCLAW:
        return False
    if not OPENCLAW_BASE_URL:
        logger.warning(
            "USE_OPENCLAW is true but OPENCLAW_BASE_URL is empty — "
            "using legacy API ingestion for v2 pipeline",
        )
        return False
    return True


def _create_v2_league_service(
    fd: FootballDataClient,
    af: ApiFootballClient,
    *,
    prefer_openclaw: bool = False,
) -> Union[LeagueAnalysisServiceV2, OpenClawLeagueAnalysisService]:
    if prefer_openclaw and _openclaw_ingestion_enabled():
        logger.info("Using OpenClaw ingestion for v2 pipeline")
        return OpenClawLeagueAnalysisService()
    logger.info("Using legacy API ingestion for v2 pipeline")
    return LeagueAnalysisServiceV2(fd, af)


def pipeline_label() -> str:
    return "v2" if USE_V2_PIPELINE else "v1"


def process_user_query(
    user_text: str,
    fd: FootballDataClient,
    af: ApiFootballClient,
    db: Database,
    *,
    prefer_openclaw_ingestion: bool = False,
) -> str:
    """Parse user text and run v1/v2 pipeline (CLI + Telegram entry).

    ``prefer_openclaw_ingestion`` (CLI only): when True and ``USE_OPENCLAW`` +
    ``OPENCLAW_BASE_URL`` are set, v2 uses :class:`OpenClawLeagueAnalysisService`.
    Telegram leaves this False so the bot stays on legacy Football-Data ingestion.
    """
    req = parse_user_request(user_text)
    logger.info("Active pipeline: %s", pipeline_label())
    return handle_request(req, fd, af, db, prefer_openclaw_ingestion=prefer_openclaw_ingestion)


def handle_request(
    req: dict,
    fd: FootballDataClient,
    af: ApiFootballClient,
    db: Database,
    *,
    prefer_openclaw_ingestion: bool = False,
) -> str:
    req_type = req.get("type", "all_matches")
    date_str = req.get("date")

    if req_type == "stats":
        report = db.get_accuracy_report()
        return format_response(report, "stats")

    if USE_V2_PIPELINE:
        logger.info("Using pipeline v2 for request type=%s", req_type)
        return _run_v2(req, fd, af, req_type, date_str, prefer_openclaw=prefer_openclaw_ingestion)

    logger.info("Using pipeline v1 for request type=%s", req_type)
    return _run_v1(req, fd, af, db, req_type, date_str)


def _run_v1(
    req: dict,
    fd: FootballDataClient,
    af: ApiFootballClient,
    db: Database,
    req_type: str,
    date_str: Optional[str],
) -> str:
    if req_type == "single_match":
        result = analyze_single_match(req.get("home_team") or "", req.get("away_team") or "", date_str, fd, af)
        if not result:
            return "Матч не найден."
        db.save_predictions([result], is_express=False)
        data = {"match": result.match.model_dump(), "markets": [m.model_dump() for m in result.markets]}
        return format_response(data, "single_match")

    results = analyze_matches_for_date(date_str, fd, af)

    if req_type == "express":
        target = float(req.get("target_odds", 3.0) or 3.0)
        express = build_express(results, target_odds=target)
        by_match_id = {r.match.id: r for r in results}
        express_results = []
        for e in express.events:
            base = by_match_id.get(e.match.id)
            if base is not None:
                express_results.append(base.model_copy(update={"best_market": e.market}))
        db.save_predictions(express_results, is_express=True)
        data = {
            "events": [{"match": e.match.model_dump(), "market": e.market.model_dump()} for e in express.events],
            "total_odds": express.total_odds,
            "total_probability": express.total_probability,
            "target_odds": express.target_odds,
        }
        return format_response(data, "express")

    db.save_predictions(results, is_express=False)
    data = [
        {
            "match": f"{r.match.home_team.short_name} vs {r.match.away_team.short_name}",
            "competition": r.match.competition_code,
            "best_market": r.best_market.model_dump(),
            "top3": [m.model_dump() for m in r.markets[:3]],
        }
        for r in results
    ]
    return format_response({"matches": data, "date": date_str}, "all_matches")


def _run_v2(
    req: dict,
    fd: FootballDataClient,
    af: ApiFootballClient,
    req_type: str,
    date_str: Optional[str],
    *,
    prefer_openclaw: bool = False,
) -> str:
    service: LeagueV2IngestProtocol = _create_v2_league_service(
        fd, af, prefer_openclaw=prefer_openclaw,
    )

    if req_type == "single_match":
        result, err = service.analyze_match_by_teams(
            req.get("home_team") or "",
            req.get("away_team") or "",
            date_str or "",
            competition_code=req.get("competition_code"),
        )
        if err:
            return err
        if not result:
            return "Матч не найден (v2 pipeline)."
        payload = build_match_payload_from_result(result)
        return format_v2_or_llm(payload, "single_match", format_v2_single_match_text(payload))

    competition_code = req.get("competition_code")

    if req_type == "express":
        results = service.analyze_matches_for_date(date_str or "", competition_code=competition_code)
        target = float(req.get("target_odds", 3.0) or 3.0)
        if not results:
            return "Нет матчей для экспресса (v2 pipeline)."
        bet = ExpressBuilderV2().build_express(results, target_odds=target)
        if not bet:
            return "Не удалось собрать экспресс (v2): нет подходящих кандидатов."
        payload = _format_v2_express_payload(bet)
        return format_v2_or_llm(payload, "express", format_v2_express_text(payload))

    if req_type != "all_matches":
        logger.warning("Unknown v2 request type %s, treating as all_matches", req_type)

    results = service.analyze_matches_for_date(date_str or "", competition_code=competition_code)
    if not results:
        return "Нет матчей на указанную дату (v2 pipeline)."
    payload = {
        "pipeline_version": "v2",
        "date": date_str,
        "include_express": False,
        "matches": [build_match_payload_from_result(r) for r in results],
    }
    return format_v2_or_llm(payload, "all_matches", format_v2_all_matches_text(payload))


def _format_v2_match_payload(result: MatchPredictionResultV2) -> Dict[str, Any]:
    return build_match_payload_from_result(result)


def _format_v2_express_payload(bet: ExpressBetV2) -> Dict[str, Any]:
    return {
        "pipeline_version": "v2",
        "events": [
            {
                "match": {
                    "id": e.match_meta.match_id,
                    "home": e.match_meta.home_team.short_name or e.match_meta.home_team.name,
                    "away": e.match_meta.away_team.short_name or e.match_meta.away_team.name,
                    "competition": e.match_meta.competition_code,
                },
                "market": {
                    "market_key": e.market_key,
                    "probability": e.probability,
                    "book_odds": e.book_odds,
                    "label": e.label,
                    "edge": e.edge,
                },
            }
            for e in bet.events
        ],
        "total_odds": bet.total_odds,
        "total_probability": bet.total_probability,
        "target_odds": bet.target_odds,
        "within_tolerance": bet.within_tolerance,
        "selection_notes": bet.selection_notes,
    }
