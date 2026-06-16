"""CLI: pre-kickoff Brave news / coach context refresh."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.services.news_refresh_store import NewsRefreshRecord, NewsRefreshStore
from football_agent.services.openclaw_news_enrichment import enrich_match_news_from_brave
from football_agent.storage.match_key import build_match_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Brave news/coach context for a match")
    parser.add_argument("--match-url", required=True)
    parser.add_argument("--flashscore-url", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from football_agent import config

    base = (args.flashscore_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
    if not base:
        print("FLASHSCORE_SCRAPER_URL required", file=sys.stderr)
        return 1

    adapter = HttpFlashscoreScraperAdapter(base)
    facts = FlashscoreIngestionService(adapter).get_facts_for_match(args.match_url)
    if facts is None:
        print(json.dumps({"success": False, "error": "facts_unavailable"}))
        return 1

    news = enrich_match_news_from_brave(facts)
    now = datetime.now(timezone.utc)
    match_key = build_match_key(
        competition=facts.meta.competition_name or "unknown",
        kickoff_utc=facts.meta.kickoff_utc,
        home_team=facts.meta.home_team_name,
        away_team=facts.meta.away_team_name,
    )

    if news is None:
        out = {"success": True, "refreshed": False, "reason": "brave_news_disabled_or_unconfigured"}
        print(json.dumps(out, default=str))
        return 0

    record = NewsRefreshRecord(
        match_key=match_key,
        match_url=args.match_url,
        kickoff_utc=facts.meta.kickoff_utc,
        collected_at_utc=news.collected_at_utc or now,
        refreshed_at_utc=now,
        is_stale=news.is_stale,
        warnings=news.warnings,
        news_context=news,
    )
    path = NewsRefreshStore().save_current(record)
    out = {
        "success": True,
        "refreshed": news.source_count > 0,
        "match_key": match_key,
        "source_count": news.source_count,
        "confidence": news.confidence,
        "store_path": str(path),
        "warnings": news.warnings,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json else None, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
