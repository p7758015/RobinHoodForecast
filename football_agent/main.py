from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    sys.path.append(str(_Path(__file__).resolve().parents[1]))

import logging
import sys

from football_agent.config import API_FOOTBALL_KEY, FOOTBALL_DATA_API_KEY
from football_agent.data_providers.api_football_client import ApiFootballClient
from football_agent.data_providers.football_data_client import FootballDataClient
from football_agent.engine.express_builder import build_express
from football_agent.engine.match_analyzer import analyze_matches_for_date, analyze_single_match
from football_agent.llm.agent import format_response, parse_user_request
from football_agent.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def run(user_text: str) -> str:
    fd = FootballDataClient(FOOTBALL_DATA_API_KEY or "")
    af = ApiFootballClient(API_FOOTBALL_KEY or "")
    db = Database()

    req = parse_user_request(user_text)
    req_type = req.get("type", "all_matches")
    date_str = req.get("date")

    if req_type == "stats":
        report = db.get_accuracy_report()
        return format_response(report, "stats")

    if req_type == "single_match":
        result = analyze_single_match(req.get("home_team") or "", req.get("away_team") or "", date_str, fd, af)
        if not result:
            return "Матч не найден."
        db.save_predictions([result], is_express=False)
        data = {"match": result.match.model_dump(), "markets": [m.model_dump() for m in result.markets]}
        return format_response(data, "single_match")

    results = analyze_matches_for_date(date_str, fd, af)

    if req_type == "express":
        target = req.get("target_odds", 3.0) or 3.0
        express = build_express(results, target_odds=float(target))
        # save_predictions expects List[MatchAnalysisResult]
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

    # all_matches
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


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Запрос: ")
    print(run(query))

