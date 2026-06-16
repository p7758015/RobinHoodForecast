"""Debug CLI: Brave news / coach context for one match."""

from __future__ import annotations

import argparse
import json
import sys

from football_agent.flashscore.adapters.http_backend import HttpFlashscoreScraperAdapter
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.services.openclaw_news_enrichment import enrich_match_news_from_brave


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live news/coach enrichment trace")
    parser.add_argument("--match-url", required=True)
    parser.add_argument("--flashscore-url", default=None)
    args = parser.parse_args(argv)

    from football_agent import config

    base = (args.flashscore_url or config.FLASHSCORE_SCRAPER_URL or "").strip().rstrip("/")
    if not base:
        print("Set FLASHSCORE_SCRAPER_URL", file=sys.stderr)
        return 1

    facts = FlashscoreIngestionService(HttpFlashscoreScraperAdapter(base)).get_facts_for_match(args.match_url)
    if facts is None:
        print(json.dumps({"error": "facts_unavailable"}))
        return 1

    news = enrich_match_news_from_brave(facts)
    if news is None:
        print(json.dumps({"enabled": False, "reason": "brave_news_disabled"}))
        return 0

    print(json.dumps(news.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
