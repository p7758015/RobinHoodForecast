"""Apply collector bundle validation onto FlashscoreMatchFacts (additive shim)."""

from __future__ import annotations

from typing import List, Tuple

from football_agent.collectors.contracts import BLOCK_ODDS, MatchCollectionBundle
from football_agent.flashscore.models import (
    FlashscoreFormBlock,
    FlashscoreMatchFacts,
    FlashscoreStandings,
    FlashscoreTeamFormBlock,
)
from football_agent.flashscore.service import FlashscoreIngestionService


def apply_bundle_to_facts(
    facts: FlashscoreMatchFacts,
    bundle: MatchCollectionBundle,
) -> Tuple[FlashscoreMatchFacts, List[str]]:
    """
    Patch facts meta/standings/form from validated collector payloads.

    Does not change merge/snapshot contracts — only hardens factual inputs.
    """
    warnings: List[str] = list(facts.provenance.parsing_warnings or [])

    meta_block = bundle.blocks.get("match_meta")
    if meta_block and meta_block.status != "failed":
        p = meta_block.payload
        kickoff = facts.meta.kickoff_utc
        if p.get("kickoff_utc"):
            parsed_kickoff = FlashscoreIngestionService._parse_datetime(p.get("kickoff_utc"))  # noqa: SLF001
            if parsed_kickoff is not None:
                kickoff = parsed_kickoff
        meta_update = {
            "home_team_name": p.get("home_team") or facts.meta.home_team_name,
            "away_team_name": p.get("away_team") or facts.meta.away_team_name,
            "competition_name": p.get("competition_name") or facts.meta.competition_name,
            "competition_country": p.get("competition_country") or facts.meta.competition_country,
            "kickoff_utc": kickoff,
            "stage": p.get("stage") or facts.meta.stage,
            "round": p.get("round") or facts.meta.round,
        }
        if p.get("match_id"):
            meta_update["match_id"] = str(p["match_id"])
        if p.get("source_url"):
            meta_update["source_url"] = str(p["source_url"])
        facts = facts.model_copy(update={"meta": facts.meta.model_copy(update=meta_update)})

    teams_block = bundle.blocks.get("teams")
    if teams_block and teams_block.status in ("ok", "partial") and teams_block.payload:
        standings = FlashscoreStandings(**teams_block.payload)
        facts = facts.model_copy(update={"standings": standings})

    form_block = bundle.blocks.get("form")
    if form_block and form_block.status in ("ok", "partial") and form_block.payload:
        payload = form_block.payload

        def _team(side: str) -> FlashscoreTeamFormBlock | None:
            data = payload.get(side)
            if not isinstance(data, dict) or not data.get("last_n_results"):
                return None
            return FlashscoreTeamFormBlock(**data)

        home_tb = _team("home")
        away_tb = _team("away")
        if home_tb or away_tb:
            facts = facts.model_copy(update={"form": FlashscoreFormBlock(home=home_tb, away=away_tb)})

    odds_block = bundle.blocks.get(BLOCK_ODDS)
    if odds_block:
        count = int(odds_block.payload.get("market_count") or 0)
        if odds_block.status == "missing":
            warnings.append("collector_odds_missing")
        elif odds_block.status == "partial":
            warnings.append(f"collector_odds_partial_{count}_markets")
        elif odds_block.status == "ok":
            warnings.append(f"collector_odds_ok_{count}_markets")

    for block_name, result in bundle.blocks.items():
        warnings.extend(f"collector_{block_name}:{w}" for w in result.warnings)

    prov = facts.provenance.model_copy(
        update={"parsing_warnings": warnings},
    )
    return facts.model_copy(update={"provenance": prov}), warnings
