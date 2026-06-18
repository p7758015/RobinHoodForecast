"""Read-only views of persisted wave predictions (eval pool runs)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from football_agent.eval_pool.report import _in_pool_scope, _snapshot_meta
from football_agent.eval_pool.scope import resolve_pool_entry
from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.offline.evaluation_v2 import extract_settlement_identity
from football_agent.paths import DEFAULT_DB_PATH
from football_agent.storage.evaluation_repository_v2 import EvaluationRepositoryV2

RESULT_MARKET_KEYS = ("HOME_WIN", "DRAW", "AWAY_WIN")


@dataclass(frozen=True)
class WavePredictionView:
    run_id: str
    match_key: str
    date: str
    pool_key: str
    competition_name: Optional[str]
    home_team: str
    away_team: str
    p_home: Optional[float]
    p_draw: Optional[float]
    p_away: Optional[float]
    best_market_key: Optional[str]
    best_market_prob: Optional[float]
    best_market_odds: Optional[float]
    confidence: Optional[float]
    odds_home: Optional[float]
    odds_draw: Optional[float]
    odds_away: Optional[float]
    final_score: Optional[str]
    settle_status: str
    analysis_mode: Optional[str]


def _market_prob(prediction: dict, market_key: str) -> Optional[float]:
    markets = prediction.get("market_predictions")
    if isinstance(markets, list):
        for item in markets:
            if isinstance(item, dict) and str(item.get("market_key")) == market_key:
                prob = item.get("probability")
                if isinstance(prob, (int, float)):
                    return float(prob)
    return None


def _odds_price(snapshot: Optional[dict], field: str) -> Optional[float]:
    if not isinstance(snapshot, dict):
        return None
    odds = snapshot.get("odds")
    if not isinstance(odds, dict):
        return None
    block = odds.get(field)
    if isinstance(block, dict):
        price = block.get("odds")
        if isinstance(price, (int, float)) and price > 1.0:
            return float(price)
    return None


def _kickoff_date(row, snapshot: Optional[dict]) -> Optional[str]:
    if row.kickoff_utc and len(str(row.kickoff_utc)) >= 10:
        return str(row.kickoff_utc)[:10]
    meta = _snapshot_meta(snapshot or {})
    md = meta.get("match_date_utc")
    if isinstance(md, str) and len(md) >= 10:
        return md[:10]
    return None


def collect_wave_predictions(
    manifest: EvalWaveManifest,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> List[WavePredictionView]:
    """Load scored runs for a wave with 1X2 probabilities, odds, and settlement."""
    allowed_keys = tuple(manifest.league_keys)
    repo = EvaluationRepositoryV2(db_path=db_path)
    views: List[WavePredictionView] = []
    try:
        rows = list(
            repo.iter_scored_runs(
                date_from=manifest.date_from,
                date_to=f"{manifest.date_to}T23:59:59",
                limit=50000,
            )
        )
        for row in rows:
            snap = row.snapshot_json or {}
            meta = _snapshot_meta(snap)
            comp_name = meta.get("competition_name") or row.competition_code
            comp_country = meta.get("country")
            if not _in_pool_scope(
                competition_name=str(comp_name) if comp_name else None,
                competition_country=str(comp_country) if comp_country else None,
                allowed_keys=allowed_keys,
            ):
                continue

            kickoff_date = _kickoff_date(row, snap)
            if kickoff_date is None:
                continue
            if kickoff_date < manifest.date_from or kickoff_date > manifest.date_to:
                continue

            entry = resolve_pool_entry(
                str(comp_name) if comp_name else None,
                str(comp_country) if comp_country else None,
            )
            pool_key = entry.key if entry else "unknown"

            pred = row.prediction_json if isinstance(row.prediction_json, dict) else {}
            analysis_mode = pred.get("analysis_mode") if isinstance(pred, dict) else None
            if analysis_mode == "analysis_only":
                views.append(
                    WavePredictionView(
                        run_id=row.run_id,
                        match_key=row.match_key,
                        date=kickoff_date,
                        pool_key=pool_key,
                        competition_name=str(comp_name) if comp_name else None,
                        home_team=str(row.home_team or ""),
                        away_team=str(row.away_team or ""),
                        p_home=None,
                        p_draw=None,
                        p_away=None,
                        best_market_key=None,
                        best_market_prob=None,
                        best_market_odds=None,
                        confidence=None,
                        odds_home=None,
                        odds_draw=None,
                        odds_away=None,
                        final_score=None,
                        settle_status="parked",
                        analysis_mode=str(analysis_mode),
                    )
                )
                continue

            best = pred.get("best_market") if isinstance(pred.get("best_market"), dict) else {}
            confidence = pred.get("overall_confidence_score")
            conf_f = float(confidence) if isinstance(confidence, (int, float)) else None

            identity = extract_settlement_identity(
                snapshot_json=snap,
                run_home_team=row.home_team,
                run_away_team=row.away_team,
                run_kickoff_utc=row.kickoff_utc,
            )
            final_score: Optional[str] = None
            settle_status = "pending"
            if identity is not None:
                settlement = repo.resolve_settlement(
                    identity.match_date,
                    identity.home_team,
                    identity.away_team,
                )
                if settlement.resolved and settlement.home_score is not None and settlement.away_score is not None:
                    final_score = f"{settlement.home_score}-{settlement.away_score}"
                    settle_status = "settled"

            views.append(
                WavePredictionView(
                    run_id=row.run_id,
                    match_key=row.match_key,
                    date=kickoff_date,
                    pool_key=pool_key,
                    competition_name=str(comp_name) if comp_name else None,
                    home_team=str(row.home_team or ""),
                    away_team=str(row.away_team or ""),
                    p_home=_market_prob(pred, "HOME_WIN"),
                    p_draw=_market_prob(pred, "DRAW"),
                    p_away=_market_prob(pred, "AWAY_WIN"),
                    best_market_key=str(best.get("market_key")) if best.get("market_key") else None,
                    best_market_prob=float(best["probability"]) if isinstance(best.get("probability"), (int, float)) else None,
                    best_market_odds=float(best["book_odds"]) if isinstance(best.get("book_odds"), (int, float)) else None,
                    confidence=conf_f,
                    odds_home=_odds_price(snap, "home_win"),
                    odds_draw=_odds_price(snap, "draw"),
                    odds_away=_odds_price(snap, "away_win"),
                    final_score=final_score,
                    settle_status=settle_status,
                    analysis_mode=str(analysis_mode) if analysis_mode else None,
                )
            )
    finally:
        repo.close()

    views.sort(key=lambda v: (v.date, v.pool_key, v.home_team, v.away_team))
    return views


def get_wave_prediction_by_run_id(
    run_id: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[WavePredictionView]:
    """Single run lookup (any scored run; not filtered by wave manifest)."""
    repo = EvaluationRepositoryV2(db_path=db_path)
    try:
        rows = list(repo.iter_scored_runs(limit=50000))
        row = next((r for r in rows if r.run_id == run_id), None)
        if row is None:
            return None
        snap = row.snapshot_json or {}
        meta = _snapshot_meta(snap)
        comp_name = meta.get("competition_name") or row.competition_code
        comp_country = meta.get("country")
        entry = resolve_pool_entry(
            str(comp_name) if comp_name else None,
            str(comp_country) if comp_country else None,
        )
        kickoff_date = _kickoff_date(row, snap) or "?"
        pred = row.prediction_json if isinstance(row.prediction_json, dict) else {}
        if pred.get("analysis_mode") == "analysis_only":
            return WavePredictionView(
                run_id=row.run_id,
                match_key=row.match_key,
                date=kickoff_date,
                pool_key=entry.key if entry else "unknown",
                competition_name=str(comp_name) if comp_name else None,
                home_team=str(row.home_team or ""),
                away_team=str(row.away_team or ""),
                p_home=None,
                p_draw=None,
                p_away=None,
                best_market_key=None,
                best_market_prob=None,
                best_market_odds=None,
                confidence=None,
                odds_home=None,
                odds_draw=None,
                odds_away=None,
                final_score=None,
                settle_status="parked",
                analysis_mode="analysis_only",
            )
        best = pred.get("best_market") if isinstance(pred.get("best_market"), dict) else {}
        identity = extract_settlement_identity(
            snapshot_json=snap,
            run_home_team=row.home_team,
            run_away_team=row.away_team,
            run_kickoff_utc=row.kickoff_utc,
        )
        final_score = None
        settle_status = "pending"
        if identity is not None:
            settlement = repo.resolve_settlement(
                identity.match_date,
                identity.home_team,
                identity.away_team,
            )
            if settlement.resolved and settlement.home_score is not None and settlement.away_score is not None:
                final_score = f"{settlement.home_score}-{settlement.away_score}"
                settle_status = "settled"
        conf = pred.get("overall_confidence_score")
        return WavePredictionView(
            run_id=row.run_id,
            match_key=row.match_key,
            date=kickoff_date,
            pool_key=entry.key if entry else "unknown",
            competition_name=str(comp_name) if comp_name else None,
            home_team=str(row.home_team or ""),
            away_team=str(row.away_team or ""),
            p_home=_market_prob(pred, "HOME_WIN"),
            p_draw=_market_prob(pred, "DRAW"),
            p_away=_market_prob(pred, "AWAY_WIN"),
            best_market_key=str(best.get("market_key")) if best.get("market_key") else None,
            best_market_prob=float(best["probability"]) if isinstance(best.get("probability"), (int, float)) else None,
            best_market_odds=float(best["book_odds"]) if isinstance(best.get("book_odds"), (int, float)) else None,
            confidence=float(conf) if isinstance(conf, (int, float)) else None,
            odds_home=_odds_price(snap, "home_win"),
            odds_draw=_odds_price(snap, "draw"),
            odds_away=_odds_price(snap, "away_win"),
            final_score=final_score,
            settle_status=settle_status,
            analysis_mode=str(pred.get("analysis_mode")) if pred.get("analysis_mode") else None,
        )
    finally:
        repo.close()


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "  —  "
    return f"{value:5.0%}"


def _fmt_odds(value: Optional[float]) -> str:
    if value is None:
        return "  — "
    return f"{value:4.2f}"


def format_predictions_table(views: Sequence[WavePredictionView]) -> str:
    """Fixed-width terminal table."""
    header = (
        f"{'date':<10} {'pool_key':<22} {'home':<18} {'away':<18} "
        f"{'p_h':>5} {'p_d':>5} {'p_a':>5} "
        f"{'best':<14} {'p*':>5} {'odds':>5} {'conf':>5} "
        f"{'score':>5} {'stl':<7} run_id"
    )
    lines = [
        f"Wave predictions ({len(views)} runs)",
        "",
        header,
        "-" * len(header),
    ]
    for v in views:
        if v.settle_status == "parked":
            lines.append(
                f"{v.date:<10} {v.pool_key:<22} {v.home_team[:18]:<18} {v.away_team[:18]:<18} "
                f"{'—':>5} {'—':>5} {'—':>5} "
                f"{'(parked)':<14} {'—':>5} {'—':>5} {'—':>5} "
                f"{'—':>5} {'parked':<7} {v.run_id[:8]}"
            )
            continue
        best_key = (v.best_market_key or "")[:14]
        lines.append(
            f"{v.date:<10} {v.pool_key:<22} {v.home_team[:18]:<18} {v.away_team[:18]:<18} "
            f"{_fmt_pct(v.p_home)} {_fmt_pct(v.p_draw)} {_fmt_pct(v.p_away)} "
            f"{best_key:<14} {_fmt_pct(v.best_market_prob)} {_fmt_odds(v.best_market_odds)} "
            f"{_fmt_pct(v.confidence)} "
            f"{(v.final_score or '—'):>5} {v.settle_status:<7} {v.run_id[:8]}"
        )
    lines.append("")
    lines.append("Columns: p_h/p_d/p_a = model 1X2; best/p*/odds = best market; conf = overall confidence; stl = settle status")
    return "\n".join(lines)


def format_prediction_detail(view: WavePredictionView) -> str:
    lines = [
        f"Run {view.run_id}",
        f"Match key: {view.match_key}",
        f"Date: {view.date}  Pool: {view.pool_key}",
        f"Competition: {view.competition_name or '—'}",
        f"Fixture: {view.home_team} vs {view.away_team}",
        "",
        "1X2 model:",
        f"  HOME_WIN: {_fmt_pct(view.p_home)}  (book {_fmt_odds(view.odds_home)})",
        f"  DRAW:     {_fmt_pct(view.p_draw)}  (book {_fmt_odds(view.odds_draw)})",
        f"  AWAY_WIN: {_fmt_pct(view.p_away)}  (book {_fmt_odds(view.odds_away)})",
        "",
        f"Best market: {view.best_market_key or '—'}  p={_fmt_pct(view.best_market_prob).strip()}  book={_fmt_odds(view.best_market_odds)}",
        f"Confidence: {_fmt_pct(view.confidence).strip()}",
        f"Settlement: {view.settle_status}" + (f"  score {view.final_score}" if view.final_score else ""),
    ]
    return "\n".join(lines)


def format_predictions_markdown(views: Sequence[WavePredictionView], *, manifest: EvalWaveManifest) -> str:
    lines = [
        f"# Predictions — {manifest.label}",
        "",
        f"- Wave: `{manifest.wave_name}`",
        f"- Dates: {manifest.date_from} — {manifest.date_to}",
        f"- Runs: {len(views)}",
        "",
        "| date | pool | home | away | p_home | p_draw | p_away | best | p_best | odds | conf | score | status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in views:
        lines.append(
            "| {date} | {pool} | {home} | {away} | {ph} | {pd} | {pa} | {best} | {pb} | {odds} | {conf} | {score} | {stl} |".format(
                date=v.date,
                pool=v.pool_key,
                home=v.home_team.replace("|", "/"),
                away=v.away_team.replace("|", "/"),
                ph=_fmt_pct(v.p_home).strip(),
                pd=_fmt_pct(v.p_draw).strip(),
                pa=_fmt_pct(v.p_away).strip(),
                best=v.best_market_key or "—",
                pb=_fmt_pct(v.best_market_prob).strip(),
                odds=_fmt_odds(v.best_market_odds).strip(),
                conf=_fmt_pct(v.confidence).strip(),
                score=v.final_score or "—",
                stl=v.settle_status,
            )
        )
    return "\n".join(lines)


def predictions_to_json(views: Sequence[WavePredictionView]) -> List[Dict[str, Any]]:
    return [asdict(v) for v in views]
