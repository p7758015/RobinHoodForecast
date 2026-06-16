"""
One-shot LeagueScorerV2 evaluation + factor inspection report.

Usage:
  python -m football_agent.debug.scorer_evaluation_report
  python -m football_agent.debug.scorer_evaluation_report --json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from football_agent.analysis_merge.merge import merge_match_context_v2
from football_agent.debug.scorer_inspection import build_scorer_inspection, rescore_snapshot
from football_agent.domain.competition_context import CompetitionContextClass
from football_agent.domain.enums_v2 import SeasonPhase, TournamentType
from football_agent.flashscore.adapters.gustavofaria_backend import FixtureFileFlashscoreAdapter
from football_agent.flashscore.models import FlashscoreMatchFacts, FlashscoreMeta, FlashscoreProvenance
from football_agent.flashscore.service import FlashscoreIngestionService
from football_agent.normalizers.merged_snapshot_builder_v2 import MergedSnapshotBuilderV2
from football_agent.odds.adapters.fixture_backend import FixtureFileOddsAdapter
from football_agent.odds.service import OddsIngestionService
from football_agent.offline.v2_calibration_runner import run_v2_batch_persist_and_evaluate
from football_agent.openclaw_context.adapters.fixture_backend import FixtureFileOpenClawContextAdapter
from football_agent.openclaw_context.service import OpenClawContextIngestionService
from football_agent.scorers.league_scorer_v2 import LeagueScorerV2
from football_agent.services.competition_classifier import CompetitionClassification
from football_agent.services.scoring_service_v2 import ScoringServiceV2
from football_agent.tests.test_scorer_v2 import HOME, _coach, _schedule, make_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "data"
BASELINE_REPORT = Path(__file__).resolve().parents[1] / "data" / "reports" / "stage2_baseline_full.json"

# Legacy flat weights (pre blueprint phase table) for delta comparison on same snapshots.
_LEGACY_WEIGHTS = {
    "baseline_strength": 0.22,
    "current_form": 0.20,
    "motivation": 0.22,
    "squad_availability": 0.14,
    "coach_factor": 0.12,
    "schedule_context": 0.10,
}
_LEGACY_H2H = 0.12


def _legacy_team_total(scoring, season_progress: float, h2h_bias: float) -> float:
    fs = scoring.factor_scores
    w = dict(_LEGACY_WEIGHTS)
    w["motivation"] *= 0.85 + 0.30 * season_progress
    total = sum(w[k] * getattr(fs, k) for k in _LEGACY_WEIGHTS)
    total += _LEGACY_H2H * max(-0.3, min(0.3, h2h_bias))
    return max(0.0, min(1.0, total))


def _score_fixture_pipeline(
    flashscore_stem: str,
    *,
    odds_stem: Optional[str] = "odds_sample",
    openclaw_stem: Optional[str] = None,
    classification: Optional[CompetitionClassification] = None,
) -> Any:
    fs = FlashscoreIngestionService(FixtureFileFlashscoreAdapter(FIXTURES))
    facts = fs.get_facts_for_match(flashscore_stem)
    if facts is None:
        raise FileNotFoundError(flashscore_stem)
    oc = None
    if openclaw_stem:
        oc = OpenClawContextIngestionService(
            FixtureFileOpenClawContextAdapter(FIXTURES),
        ).get_context_for_fixture(openclaw_stem)
    odds = None
    if odds_stem:
        odds = OddsIngestionService(FixtureFileOddsAdapter(FIXTURES)).get_odds_for_fixture(odds_stem)
    merged = merge_match_context_v2(facts=facts, openclaw_context=oc, odds_context=odds)
    snapshot, report = MergedSnapshotBuilderV2().build_with_report(merged)
    clf = classification
    if clf is None and flashscore_stem == "flashscore_sample_league_match":
        clf = CompetitionClassification(
            category=CompetitionContextClass.LEAGUE,
            tournament_type=TournamentType.LEAGUE_REGULAR,
            confidence="high",
            signals=["fixture"],
        )
    scored = ScoringServiceV2().score_snapshot_with_report(snapshot, report, classification=clf)
    return scored, build_scorer_inspection(scored)


def _batch_fixture_items() -> List[Dict[str, Any]]:
    return [
        {
            "flashscore_stem": "flashscore_sample_league_match",
            "odds_stem": "odds_sample",
            "openclaw_stem": "openclaw_context_sample",
            "home_score": 1,
            "away_score": 1,
        },
        {"flashscore_stem": "flashscore_botola_sample_match", "home_score": 2, "away_score": 1},
        {"flashscore_stem": "flashscore_botola_sample_match_2", "home_score": 0, "away_score": 0},
        {"flashscore_stem": "flashscore_botola_sample_match_3", "home_score": 1, "away_score": 2},
    ]


def _representative_scenarios() -> List[Dict[str, Any]]:
    scenarios: List[Dict[str, Any]] = []

    # 1) Top-5 league fixture (Serie A)
    scored, insp = _score_fixture_pipeline("flashscore_sample_league_match")
    scenarios.append({"label": "top5_league_serie_a", "inspection": insp})

    # 2) Late season / high motivation (synthetic on enriched snapshot)
    late = make_snapshot(
        home_baseline=0.55,
        away_baseline=0.52,
        home_motivation=0.92,
        away_motivation=0.88,
        home_form=0.65,
        away_form=0.60,
        season_phase=SeasonPhase.FINAL_RUN_IN,
        season_progress=0.92,
        confidence=0.72,
        with_odds=True,
    )
    late_scored, late_insp = rescore_snapshot(late)
    scenarios.append({"label": "late_season_high_motivation_synthetic", "inspection": late_insp})

    # 3) New coach first match
    new_coach = make_snapshot(home_first_match=True, home_xi=0.25, confidence=0.52, with_odds=True)
    new_coach.home_schedule = _schedule(HOME, pre_big=0.0, rotation=0.55)
    nc_scored, nc_insp = rescore_snapshot(new_coach)
    scenarios.append({"label": "new_coach_first_match_synthetic", "inspection": nc_insp})

    # 4) Pre big-match schedule risk
    sched = make_snapshot(home_baseline=0.85, away_baseline=0.40, confidence=0.68, with_odds=True)
    sched.home_schedule = _schedule(HOME, pre_big=0.35, days_to_next=2, rotation=0.65)
    sch_scored, sch_insp = rescore_snapshot(sched)
    scenarios.append({"label": "pre_big_match_schedule_risk_synthetic", "inspection": sch_insp})

    # 5) Non-top-5 league (Botola)
    botola_scored, botola_insp = _score_fixture_pipeline("flashscore_botola_sample_match")
    scenarios.append({"label": "non_top5_botola", "inspection": botola_insp})

    # 6) Parked domestic cup
    cup_facts = FlashscoreMatchFacts(
        meta=FlashscoreMeta(
            match_id="cup-eval",
            source_url="https://example.com",
            competition_name="FA Cup",
            home_team_name="Arsenal",
            away_team_name="Chelsea",
            tournament_type=TournamentType.DOMESTIC_CUP,
        ),
        provenance=FlashscoreProvenance(scraper_backend_name="test"),
    )
    cup_merged = merge_match_context_v2(facts=cup_facts, openclaw_context=None, odds_context=None)
    cup_snap, cup_rep = MergedSnapshotBuilderV2().build_with_report(cup_merged)
    cup_clf = CompetitionClassification(
        category=CompetitionContextClass.DOMESTIC_CUP,
        tournament_type=TournamentType.DOMESTIC_CUP,
        confidence="high",
        signals=["fixture"],
    )
    cup_scored = ScoringServiceV2().score_snapshot_with_report(
        cup_snap, cup_rep, classification=cup_clf,
    )
    scenarios.append(
        {
            "label": "parked_domestic_cup",
            "inspection": build_scorer_inspection(cup_scored),
        },
    )

    # Legacy delta on Serie A fixture
    legacy_home = _legacy_team_total(
        scored.prediction.home_scoring,
        float(scored.snapshot.match_meta.season_progress or 0),
        scored.snapshot.h2h_context.h2h_context_bias,
    )
    legacy_away = _legacy_team_total(
        scored.prediction.away_scoring,
        float(scored.snapshot.match_meta.season_progress or 0),
        -scored.snapshot.h2h_context.h2h_context_bias,
    )
    scenarios[0]["legacy_delta"] = {
        "home_r_legacy": round(legacy_home, 4),
        "away_r_legacy": round(legacy_away, 4),
        "home_r_current": round(scored.prediction.home_scoring.factor_scores.total_score + 0.04, 4),
        "away_r_current": round(scored.prediction.away_scoring.factor_scores.total_score, 4),
        "best_market_legacy_note": "legacy probabilities not re-run (R delta only)",
    }

    return scenarios


def _aggregate_run_metrics(scored_runs: List[Any]) -> Dict[str, Any]:
    confs: List[float] = []
    markets: List[str] = []
    express_allow = 0
    express_caution = 0
    low_conf = 0
    for s in scored_runs:
        if s.scoring_skipped:
            continue
        confs.append(float(s.prediction.overall_confidence_score))
        if s.prediction.best_market:
            markets.append(s.prediction.best_market.market_key)
        if s.prediction.overall_confidence_score < 0.45:
            low_conf += 1
        if s.prediction.express_safety.allow_for_express:
            express_allow += 1
        elif s.prediction.express_safety.penalty_score >= 0.22:
            express_caution += 1
    return {
        "league_scored_count": len(confs),
        "confidence_avg": round(sum(confs) / len(confs), 4) if confs else None,
        "confidence_min": round(min(confs), 4) if confs else None,
        "confidence_max": round(max(confs), 4) if confs else None,
        "low_confidence_share": round(low_conf / len(confs), 4) if confs else None,
        "best_market_distribution": dict(Counter(markets)),
        "express_allow_count": express_allow,
        "express_caution_or_higher": express_caution,
    }


def build_report(*, db_path: Optional[Path] = None) -> Dict[str, Any]:
    use_temp = db_path is None
    if use_temp:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = Path(tmp.name)
        tmp.close()

    batch_eval = run_v2_batch_persist_and_evaluate(
        FIXTURES,
        _batch_fixture_items(),
        db_path=db_path,
    )

    baseline: Optional[Dict[str, Any]] = None
    if BASELINE_REPORT.exists():
        raw = BASELINE_REPORT.read_bytes()
        text = raw.decode("utf-8-sig") if not raw.startswith(b"\xff\xfe") else raw.decode("utf-16")
        baseline = json.loads(text)

    scenarios = _representative_scenarios()

    # Re-score batch snapshots for aggregate metrics (league only)
    league_scored = []
    for item in _batch_fixture_items():
        try:
            s, _ = _score_fixture_pipeline(item["flashscore_stem"], odds_stem=item.get("odds_stem"))
            if not s.scoring_skipped:
                league_scored.append(s)
        except Exception:
            pass

    parked_count = sum(1 for sc in scenarios if sc["inspection"].get("scoring_skipped"))
    league_count = len(scenarios) - parked_count

    report: Dict[str, Any] = {
        "entry_points": {
            "offline_cli": "python -m football_agent.debug.offline_evaluation_trace",
            "batch_persist": "offline.v2_calibration_runner.run_v2_batch_persist_and_evaluate",
            "live_trace": "python -m football_agent.debug.merged_scoring_trace",
            "inspection": "football_agent.debug.scorer_inspection.build_scorer_inspection",
        },
        "batch_evaluation": batch_eval,
        "aggregate_league_metrics": _aggregate_run_metrics(league_scored),
        "routing_sanity": {
            "representative_scenarios": len(scenarios),
            "parked_scenarios": parked_count,
            "league_scenarios": league_count,
        },
        "representative_inspections": scenarios,
        "baseline_comparison": {
            "source": str(BASELINE_REPORT) if baseline else None,
            "note": "stage2 baseline used flat legacy weights; current run uses blueprint phase weights",
            "baseline_best_market_avg_prob": (
                baseline.get("evaluation", {}).get("calibration", {}).get("buckets")
                if baseline
                else None
            ),
        },
    }

    if baseline and batch_eval.get("evaluation"):
        old_hr = baseline["evaluation"]["metrics"].get("best_market_hit_rate")
        new_hr = batch_eval["evaluation"]["metrics"].get("best_market_hit_rate")
        old_buckets = baseline["evaluation"]["calibration"]["buckets"]
        new_buckets = batch_eval["evaluation"]["calibration"]["buckets"]
        report["delta_vs_stage2_baseline"] = {
            "best_market_hit_rate_old": old_hr,
            "best_market_hit_rate_new": new_hr,
            "calibration_buckets_old": old_buckets,
            "calibration_buckets_new": new_buckets,
            "interpretation": (
                "Same 4 fixture runs; differences reflect scorer tuning + enriched snapshot builder, "
                "not new historical data."
            ),
        }

    if use_temp and db_path is not None:
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass

    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="scorer_evaluation_report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0

    ev = report["batch_evaluation"]["evaluation"]
    counts = ev.get("counts", {})
    metrics = ev.get("metrics", {})
    agg = report["aggregate_league_metrics"]
    print("=== LeagueScorerV2 evaluation report ===")
    print(f"Evaluable runs: {counts.get('evaluable_runs_total')} / scored {counts.get('scored_runs_total')}")
    print(f"Settled coverage: {metrics.get('settled_coverage')} hit_rate: {metrics.get('best_market_hit_rate')}")
    print(f"Odds coverage: {metrics.get('odds_coverage')}")
    print(f"League aggregate confidence avg: {agg.get('confidence_avg')} low-conf share: {agg.get('low_confidence_share')}")
    print(f"Best market mix: {agg.get('best_market_distribution')}")
    print(f"Express allow: {agg.get('express_allow_count')} caution+: {agg.get('express_caution_or_higher')}")
    if "delta_vs_stage2_baseline" in report:
        d = report["delta_vs_stage2_baseline"]
        print(f"Delta vs stage2 baseline hit_rate: {d.get('best_market_hit_rate_old')} -> {d.get('best_market_hit_rate_new')}")
    print("\n--- Representative inspections (top factors) ---")
    for sc in report["representative_inspections"]:
        insp = sc["inspection"]
        print(f"\n[{sc['label']}] route={insp.get('routing', {}).get('route', 'league')} mode={insp.get('analysis_mode')}")
        if insp.get("best_market"):
            bm = insp["best_market"]
            print(f"  best: {bm['market_key']} p={bm['probability']}")
        for side in ("home", "away"):
            block = insp.get(side) or {}
            shares = (block.get("weighted_contributions") or {}).get("share_pct") or {}
            if shares:
                top = sorted(shares.items(), key=lambda x: x[1], reverse=True)[:3]
                print(f"  {side} phase={block.get('season_phase')} top_factors={top} overrides={block.get('special_overrides')}")
        if sc.get("legacy_delta"):
            print(f"  legacy R delta: {sc['legacy_delta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
