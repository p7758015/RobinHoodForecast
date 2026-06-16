"""Bridge collector odds block → downstream MatchOddsContext (Odds B)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional, Tuple, Any

from football_agent.collectors.contracts import BLOCK_ODDS, MatchCollectionBundle
from football_agent.flashscore.models import FlashscoreMatchFacts
from football_agent.odds.models import (
    MatchOddsContext,
    OddsMarketQuote,
    OddsMarketsBlock,
    OddsMeta,
    OddsProvenance,
    QuoteConfidence,
)
from football_agent.odds.service import MARKET_FIELDS

# Collector canonical keys → odds v1 service field names (DRAW intentionally omitted).
COLLECTOR_TO_SERVICE_MARKET: dict[str, str] = {
    "HOME_WIN": "home_win",
    "AWAY_WIN": "away_win",
    "HOME_OR_DRAW": "double_chance_1x",
    "AWAY_OR_DRAW": "double_chance_x2",
    "BTTS_YES": "btts_yes",
    "OVER_1_5": "over_1_5",
    "UNDER_3_5": "under_3_5",
}

OddsBridgeSource = Literal["none", "collector", "enrichment", "mixed", "fixture"]

SOURCE_FLASHSCORE_COLLECTOR = "flashscore_collector"


def collector_odds_to_context(
    bundle: Optional[MatchCollectionBundle],
    facts: FlashscoreMatchFacts,
) -> Optional[MatchOddsContext]:
    """
    Map collector odds block into MatchOddsContext.

    Returns None when block is missing/empty (fail-soft).
    """
    if bundle is None:
        return None

    block = bundle.blocks.get(BLOCK_ODDS)
    if block is None or block.status == "missing":
        return None

    payload = block.payload if isinstance(block.payload, dict) else {}
    raw_markets = payload.get("markets")
    if not isinstance(raw_markets, dict) or not raw_markets:
        return None

    now = datetime.now(timezone.utc)
    bookmaker = str(payload.get("bookmaker") or "flashscore")
    quote_conf = _block_confidence_to_quote(block.confidence)

    market_kwargs: dict[str, Optional[OddsMarketQuote]] = {name: None for name in MARKET_FIELDS}
    bridge_warnings: List[str] = list(block.warnings or [])

    for collector_key, entry in raw_markets.items():
        service_key = COLLECTOR_TO_SERVICE_MARKET.get(str(collector_key))
        if service_key is None:
            if str(collector_key) == "DRAW":
                bridge_warnings.append("collector_odds_draw_not_in_v1_contract")
            continue
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        try:
            odds_value = float(value)
        except (TypeError, ValueError):
            bridge_warnings.append(f"collector_odds_bridge_invalid:{collector_key}")
            continue
        if odds_value <= 1.0:
            bridge_warnings.append(f"collector_odds_bridge_invalid:{collector_key}")
            continue
        is_derived = bool(entry.get("derived"))
        if is_derived:
            bridge_warnings.append(f"collector_odds_bridge_derived:{collector_key}")
            derived_from = entry.get("derived_from")
            if isinstance(derived_from, list) and derived_from:
                bridge_warnings.append(
                    f"collector_odds_bridge_derived_from:{collector_key}={','.join(str(x) for x in derived_from)}",
                )
        market_kwargs[service_key] = OddsMarketQuote(
            odds_value=odds_value,
            bookmaker_name=bookmaker,
            selection_name_raw=(
                f"derived:{entry.get('raw_label') or collector_key}"
                if is_derived
                else str(entry.get("raw_label") or collector_key)
            ),
            confidence="LOW" if is_derived else quote_conf,
        )

    markets = OddsMarketsBlock(**market_kwargs)
    if not any(getattr(markets, name) is not None for name in MARKET_FIELDS):
        return None

    missing_markets = [name for name in MARKET_FIELDS if getattr(markets, name) is None]
    filled = [name for name in MARKET_FIELDS if getattr(markets, name) is not None]

    meta = OddsMeta(
        fixture_id=str(facts.meta.match_id or bundle.match_key or ""),
        match_id=facts.meta.match_id,
        source=SOURCE_FLASHSCORE_COLLECTOR,
        home_team=facts.meta.home_team_name,
        away_team=facts.meta.away_team_name,
        competition_name=facts.meta.competition_name,
        kickoff_utc=facts.meta.kickoff_utc,
        collected_at_utc=block.collected_at_utc or now,
        source_url=facts.meta.source_url,
        odds_format="DECIMAL",
    )

    prov = OddsProvenance(
        backend_name=SOURCE_FLASHSCORE_COLLECTOR,
        backend_version="odds-a",
        adapter_version="collector-odds-bridge-v1",
        collected_at_utc=block.collected_at_utc or now,
        blocks_present=["collector_odds"],
        missing_blocks=[],
        missing_markets=missing_markets,
        extraction_warnings=bridge_warnings,
        freshness_status="fresh",
        is_stale=False,
    )
    prov.extraction_warnings.append(f"collector_odds_filled:{','.join(filled) or 'none'}")
    derived_keys = [
        str(k)
        for k, entry in raw_markets.items()
        if isinstance(entry, dict) and entry.get("derived")
    ]
    if derived_keys:
        prov.extraction_warnings.append(f"collector_odds_derived:{','.join(derived_keys)}")
    prov.extraction_warnings.append(f"collector_odds_confidence:{block.confidence}")

    ctx = MatchOddsContext(meta=meta, markets=markets, provenance=prov)
    from football_agent.odds.coverage import enrich_odds_context_with_coverage

    return enrich_odds_context_with_coverage(ctx)


def merge_collector_and_enrichment_odds(
    collector: Optional[MatchOddsContext],
    enrichment: Optional[MatchOddsContext],
) -> Tuple[Optional[MatchOddsContext], List[str], OddsBridgeSource]:
    """
    Merge odds contexts with collector as primary for overlapping markets.

    Enrichment fills gaps only. Transparent warnings on overlap/conflict.
    """
    warnings: List[str] = []
    if collector is None and enrichment is None:
        return None, warnings, "none"
    if collector is None:
        from football_agent.odds.coverage import enrich_odds_context_with_coverage

        return enrich_odds_context_with_coverage(enrichment), warnings, "enrichment"
    if enrichment is None:
        warnings.append("odds_bridge:collector_only")
        return collector, warnings, "collector"

    merged_kwargs: dict[str, Optional[OddsMarketQuote]] = {}
    collector_filled: List[str] = []
    enrichment_filled: List[str] = []

    for field in MARKET_FIELDS:
        c_q = getattr(collector.markets, field)
        e_q = getattr(enrichment.markets, field)
        if c_q is not None:
            collector_filled.append(field)
        if e_q is not None:
            enrichment_filled.append(field)
        chosen = c_q or e_q
        if c_q is not None and e_q is not None and abs(c_q.odds_value - e_q.odds_value) > 0.01:
            warnings.append(f"odds_bridge_conflict:{field}:collector_preferred")
        merged_kwargs[field] = chosen

    warnings.append("odds_bridge:source_mixed")
    warnings.append(f"odds_bridge_collector_markets:{','.join(collector_filled) or 'none'}")
    warnings.append(f"odds_bridge_enrichment_markets:{','.join(enrichment_filled) or 'none'}")

    missing_markets = [name for name in MARKET_FIELDS if merged_kwargs.get(name) is None]
    merge_warnings = list(collector.provenance.extraction_warnings or [])
    merge_warnings.extend(enrichment.provenance.extraction_warnings or [])
    merge_warnings.extend(warnings)

    meta = collector.meta.model_copy(
        update={"source": f"{SOURCE_FLASHSCORE_COLLECTOR}+enrichment"},
    )
    prov = OddsProvenance(
        backend_name="mixed",
        backend_version="odds-b",
        adapter_version="collector-odds-bridge-v1",
        collected_at_utc=collector.provenance.collected_at_utc,
        blocks_present=["collector_odds", "enrichment_odds"],
        missing_blocks=[],
        missing_markets=missing_markets,
        extraction_warnings=merge_warnings,
    )

    merged = MatchOddsContext(
        meta=meta,
        markets=OddsMarketsBlock(**merged_kwargs),
        provenance=prov,
    )
    from football_agent.odds.coverage import enrich_odds_context_with_coverage

    return enrich_odds_context_with_coverage(merged), warnings, "mixed"


def _teams_canonical_match(facts: FlashscoreMatchFacts, odds: MatchOddsContext) -> bool:
    from football_agent.normalizers.team_name_resolver import canonical_team_key, normalize_team_name

    def canon(name: str) -> str:
        return canonical_team_key(normalize_team_name(name or ""))

    home_fs = canon(facts.meta.home_team_name)
    away_fs = canon(facts.meta.away_team_name)
    home_od = canon(odds.meta.home_team)
    away_od = canon(odds.meta.away_team)
    if not home_fs or not away_fs or not home_od or not away_od:
        return False
    return home_fs == home_od and away_fs == away_od


def align_odds_meta_to_facts(
    facts: FlashscoreMatchFacts,
    odds: Optional[MatchOddsContext],
) -> Optional[MatchOddsContext]:
    """Fill missing odds meta from Flashscore facts when teams canonically match."""
    if odds is None:
        return None
    if not _teams_canonical_match(facts, odds):
        return odds

    meta_updates: dict[str, Any] = {}
    if facts.meta.match_id:
        if not odds.meta.match_id:
            meta_updates["match_id"] = facts.meta.match_id
        if not odds.meta.fixture_id:
            meta_updates["fixture_id"] = str(facts.meta.match_id)
    if facts.meta.kickoff_utc and not odds.meta.kickoff_utc:
        meta_updates["kickoff_utc"] = facts.meta.kickoff_utc
    if facts.meta.competition_name and not odds.meta.competition_name:
        meta_updates["competition_name"] = facts.meta.competition_name

    if not meta_updates:
        return odds
    return odds.model_copy(update={"meta": odds.meta.model_copy(update=meta_updates)})


def build_odds_bundle_from_flashscore_raw(
    raw: dict,
    *,
    match_key: str = "flashscore",
) -> Optional[MatchCollectionBundle]:
    """Lightweight odds-only collector bundle from enriched Flashscore HTTP raw."""
    if not isinstance(raw, dict) or not raw:
        return None
    from football_agent.collectors.contracts import BLOCK_ODDS, MatchRef
    from football_agent.collectors.flashscore.odds_collector import FlashscoreOddsCollector

    block = FlashscoreOddsCollector().collect(raw, MatchRef())
    if block.status == "missing":
        return None
    return MatchCollectionBundle(
        match_key=match_key,
        match_ref=MatchRef(match_id=str(raw.get("match_id") or "")),
        blocks={BLOCK_ODDS: block},
        overall_confidence=block.confidence,
        overall_status="partial" if block.status == "partial" else "ok",
    )


def resolve_pipeline_odds_context(
    *,
    facts: FlashscoreMatchFacts,
    collector_bundle: Optional[MatchCollectionBundle],
    enrichment_odds: Optional[MatchOddsContext],
    fixture_odds: Optional[MatchOddsContext] = None,
) -> Tuple[Optional[MatchOddsContext], List[str], OddsBridgeSource]:
    """Resolve final odds context for live pipeline (collector → enrichment → fixture override)."""
    collector_ctx = collector_odds_to_context(collector_bundle, facts)
    enrichment_odds = align_odds_meta_to_facts(facts, enrichment_odds)
    fixture_odds = align_odds_meta_to_facts(facts, fixture_odds)
    merged, warnings, source = merge_collector_and_enrichment_odds(collector_ctx, enrichment_odds)
    if merged is not None:
        from football_agent.odds.coverage import enrich_odds_context_with_coverage

        merged = enrich_odds_context_with_coverage(merged)
    if fixture_odds is not None:
        fixture_odds = align_odds_meta_to_facts(facts, fixture_odds)
        from football_agent.odds.coverage import enrich_odds_context_with_coverage

        fixture_odds = enrich_odds_context_with_coverage(fixture_odds)
        warnings.append("odds_bridge:fixture_override")
        return fixture_odds, warnings, "fixture"
    return merged, warnings, source


def refresh_odds_source_status(sources: dict[str, str], odds_ctx: Optional[MatchOddsContext]) -> None:
    """Upgrade odds transport status when bridge produced a usable context."""
    if odds_ctx is None:
        return
    from football_agent.services.enrichment_contract import SOURCE_OK, SOURCE_PARTIAL

    missing = odds_ctx.provenance.missing_markets or []
    filled_count = len(MARKET_FIELDS) - len(missing)
    if filled_count <= 0:
        return
    if len(missing) == 0:
        sources["odds"] = SOURCE_OK
    else:
        sources["odds"] = SOURCE_PARTIAL


def _block_confidence_to_quote(confidence: float) -> QuoteConfidence:
    if confidence >= 0.75:
        return "HIGH"
    if confidence >= 0.45:
        return "MEDIUM"
    if confidence > 0.0:
        return "LOW"
    return "UNKNOWN"
