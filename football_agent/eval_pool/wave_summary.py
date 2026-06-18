"""Human-readable and markdown summaries for eval wave runs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from football_agent.eval_pool.wave_manifest import EvalWaveManifest
from football_agent.eval_pool.wave_predictions import collect_wave_predictions, format_predictions_markdown, predictions_to_json


def build_wave_cli_summary(
    manifest: EvalWaveManifest,
    *,
    accumulate: Optional[Dict[str, Any]] = None,
    update_results: Optional[Dict[str, Any]] = None,
    settlement: Optional[Dict[str, Any]] = None,
    coverage_report: Optional[Dict[str, Any]] = None,
    calibration: Optional[Dict[str, Any]] = None,
    output_paths: Optional[Dict[str, str]] = None,
) -> str:
    lines = [
        f"Eval wave: {manifest.label}",
        f"Wave id: {manifest.wave_name}",
        f"Dates: {manifest.date_from} .. {manifest.date_to}",
        f"Leagues: {', '.join(manifest.league_keys)}",
    ]
    if manifest.expected_matches:
        lines.append(f"Expected matches (plan): {manifest.expected_matches}")

    if accumulate:
        lines.extend(_section_accumulate(accumulate))
    if update_results:
        lines.extend(_section_update_results(update_results))
    if settlement:
        lines.extend(_section_settlement(settlement))
    if coverage_report:
        lines.extend(_section_coverage(coverage_report))
    if calibration:
        lines.extend(_section_calibration(calibration))

    if output_paths:
        lines.append("")
        lines.append("Artifacts:")
        for key, path in output_paths.items():
            lines.append(f"  • {key}: {path}")

    return "\n".join(lines)


def build_wave_markdown(
    manifest: EvalWaveManifest,
    payload: Dict[str, Any],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    acc = payload.get("accumulate") or {}
    upd = payload.get("update_results") or {}
    stl = payload.get("settlement") or {}
    cov = payload.get("coverage_report") or {}
    cal = payload.get("calibration") or {}
    diag = (cal.get("diagnostics") or {}) if cal else {}

    lines = [
        f"# Eval wave report — {manifest.label}",
        "",
        f"- **Wave:** `{manifest.wave_name}`",
        f"- **Generated:** {now}",
        f"- **Dates:** {manifest.date_from} — {manifest.date_to}",
        f"- **Expected matches (plan):** {manifest.expected_matches or '—'}",
        "",
        "## Accumulation",
        "",
        f"- Fixtures in scope: **{acc.get('fixtures_in_scope', '—')}**",
        f"- League scored / persisted: **{acc.get('league_full_scored', '—')}** / **{acc.get('persist_success', '—')}**",
        f"- Discovery fixtures added: **{acc.get('discovery_fixtures_added', 0)}**",
        f"- Parked skipped: **{acc.get('parked_or_non_league_skipped', '—')}**",
        f"- Errors: **{len(acc.get('errors') or [])}**",
        "",
        "## Results fetch",
        "",
        f"- Results saved to DB: **{upd.get('results_saved', '—')}**",
        f"- Finished in scope: **{upd.get('finished_in_scope', '—')}**",
        f"- Not finished yet: **{upd.get('skipped_not_finished', '—')}**",
        "",
        "## Settlement (join predictions ↔ results)",
        "",
        f"- League scored runs: **{stl.get('league_scored_runs', '—')}**",
        f"- Settled evaluable: **{stl.get('settled_evaluable', '—')}**",
        f"- Unresolved: **{stl.get('unsettled', '—')}**",
        f"- Parked skipped: **{stl.get('parked_skipped', '—')}**",
        f"- Hit rate (settled): **{_fmt_pct(stl.get('hit_rate'))}**",
        f"- Wins / losses: **{stl.get('wins', '—')}** / **{stl.get('losses', '—')}**",
        "",
        "## Calibration review",
        "",
    ]

    sample = cal.get("sample") or {}
    lines.append(f"- Settled evaluable sample: **{sample.get('settled_evaluable_runs', '—')}**")
    lines.append(f"- Sufficient for diagnostics: **{sample.get('sufficient_for_diagnostics', '—')}**")
    lines.append(f"- Diagnostics status: **{diag.get('status', '—')}**")
    if diag.get("message"):
        lines.append(f"- Note: {diag['message']}")

    lines.append("")
    lines.append("### Confidence buckets")
    lines.append("")
    lines.append("| Bucket | N | Hit rate | Avg prob | ROI |")
    lines.append("|--------|---|----------|----------|-----|")
    for b in cal.get("confidence_buckets") or []:
        lines.append(
            f"| {b.get('confidence_bucket')} | {b.get('count')} | {b.get('hit_rate')} | "
            f"{b.get('avg_predicted_probability')} | {b.get('roi_mean_profit')} |"
        )

    lines.append("")
    lines.append("### Market buckets")
    lines.append("")
    lines.append("| Market | N | Hit rate | Avg conf |")
    lines.append("|--------|---|----------|----------|")
    for b in cal.get("market_buckets") or []:
        lines.append(
            f"| {b.get('market_key')} | {b.get('count')} | {b.get('hit_rate')} | {b.get('avg_confidence')} |"
        )

    lines.append("")
    lines.append("### League buckets")
    lines.append("")
    lines.append("| League | N | Hit rate | Odds cov | Low conf |")
    lines.append("|--------|---|----------|----------|----------|")
    for b in cal.get("league_buckets") or []:
        lines.append(
            f"| {b.get('pool_key')} | {b.get('count')} | {b.get('hit_rate')} | "
            f"{b.get('odds_coverage_share')} | {b.get('low_confidence_share')} |"
        )

    findings = diag.get("findings") or []
    if findings:
        lines.append("")
        lines.append("### Diagnostics findings")
        lines.append("")
        for f in findings:
            lines.append(f"- **[{f.get('type')}]** {f.get('scope')}: {f.get('detail')}")

    notes = manifest.notes
    if notes:
        lines.append("")
        lines.append("## Wave plan notes")
        lines.append("")
        for day, text in sorted(notes.items()):
            lines.append(f"- {day}: {text}")

    return "\n".join(lines) + "\n"


def _section_accumulate(acc: Dict[str, Any]) -> list[str]:
    return [
        "",
        "Accumulation:",
        f"  fixtures in scope: {acc.get('fixtures_in_scope')}",
        f"  league scored: {acc.get('league_full_scored')}",
        f"  persisted: {acc.get('persist_success')}",
        f"  discovery added: {acc.get('discovery_fixtures_added', 0)}",
        f"  warnings: {len(acc.get('discovery_warnings') or [])}",
        f"  errors: {len(acc.get('errors') or [])}",
    ]


def _section_update_results(upd: Dict[str, Any]) -> list[str]:
    return [
        "",
        "Results fetch:",
        f"  results saved: {upd.get('results_saved')}",
        f"  finished in scope: {upd.get('finished_in_scope')}",
        f"  not finished: {upd.get('skipped_not_finished')}",
        f"  no score: {upd.get('skipped_no_score')}",
    ]


def _section_settlement(stl: Dict[str, Any]) -> list[str]:
    return [
        "",
        "Settlement:",
        f"  league scored: {stl.get('league_scored_runs')}",
        f"  settled evaluable: {stl.get('settled_evaluable')}",
        f"  unresolved: {stl.get('unsettled')}",
        f"  parked skipped: {stl.get('parked_skipped')}",
        f"  hit rate: {_fmt_pct(stl.get('hit_rate'))}",
        f"  wins/losses: {stl.get('wins')}/{stl.get('losses')}",
    ]


def _section_coverage(cov: Dict[str, Any]) -> list[str]:
    counts = cov.get("counts") or {}
    return [
        "",
        "Pool coverage:",
        f"  pool runs: {counts.get('pool_runs')}",
        f"  league scored: {counts.get('league_scored_runs')}",
        f"  settled league scored: {counts.get('settled_league_scored_runs')}",
        f"  runs with odds: {counts.get('runs_with_odds')}",
    ]


def _section_calibration(cal: Dict[str, Any]) -> list[str]:
    sample = cal.get("sample") or {}
    diag = cal.get("diagnostics") or {}
    lines = [
        "",
        "Calibration:",
        f"  settled evaluable: {sample.get('settled_evaluable_runs')}",
        f"  sufficient sample: {sample.get('sufficient_for_diagnostics')}",
        f"  diagnostics: {diag.get('status')}",
    ]
    for b in cal.get("confidence_buckets") or []:
        lines.append(
            f"  conf {b.get('confidence_bucket')}: n={b.get('count')} hit={b.get('hit_rate')}"
        )
    if cal.get("market_buckets"):
        top = max(cal["market_buckets"], key=lambda x: x.get("count") or 0)
        lines.append(f"  top market: {top.get('market_key')} ({top.get('count')})")
    for f in diag.get("findings") or []:
        lines.append(f"  finding [{f.get('type')}]: {f.get('detail')}")
    return lines


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return str(value)


def write_wave_artifacts(
    manifest: EvalWaveManifest,
    payload: Dict[str, Any],
    *,
    output_dir: Path,
) -> Dict[str, str]:
    import json

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"{manifest.wave_name}_{stamp}"
    json_path = output_dir / f"{base}.json"
    md_path = output_dir / f"{base}.md"

    json_payload = {k: v for k, v in payload.items() if k != "prediction_views"}
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_wave_markdown(manifest, payload), encoding="utf-8")
    paths: Dict[str, str] = {"json": str(json_path), "markdown": str(md_path)}
    prediction_views = payload.get("prediction_views")
    if prediction_views:
        pred_md_path = output_dir / f"{base}_predictions.md"
        pred_md_path.write_text(
            format_predictions_markdown(prediction_views, manifest=manifest),
            encoding="utf-8",
        )
        paths["predictions_markdown"] = str(pred_md_path)
    return paths
