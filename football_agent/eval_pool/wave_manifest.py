"""Load eval wave manifests (JSON files or built-in presets)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from football_agent.paths import PACKAGE_ROOT

BUILTIN_PRESETS: Dict[str, str] = {
    "june18_21_first_batch": str(
        PACKAGE_ROOT / "data" / "eval_waves" / "june18_21_first_batch.json",
    ),
}


@dataclass(frozen=True)
class EvalWaveManifest:
    wave_name: str
    label: str
    date_from: str
    date_to: str
    league_keys: Sequence[str]
    expected_matches: Optional[int] = None
    notes: Dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "EvalWaveManifest":
        keys = data.get("league_keys") or []
        if not keys:
            raise ValueError("manifest.league_keys is required")
        return EvalWaveManifest(
            wave_name=str(data.get("wave_name") or "unnamed_wave"),
            label=str(data.get("label") or data.get("wave_name") or "Eval wave"),
            date_from=str(data["date_from"]),
            date_to=str(data["date_to"]),
            league_keys=tuple(str(k) for k in keys),
            expected_matches=int(data["expected_matches"]) if data.get("expected_matches") else None,
            notes=dict(data.get("notes") or {}),
        )


def load_wave_manifest(
    *,
    preset: Optional[str] = None,
    manifest_path: Optional[str | Path] = None,
) -> EvalWaveManifest:
    if preset and manifest_path:
        raise ValueError("Use either --preset or --manifest, not both")
    path: Optional[Path] = None
    if preset:
        rel = BUILTIN_PRESETS.get(preset)
        if not rel:
            known = ", ".join(sorted(BUILTIN_PRESETS))
            raise ValueError(f"Unknown preset {preset!r}. Known: {known}")
        path = Path(rel)
    elif manifest_path:
        path = Path(manifest_path)
    else:
        raise ValueError("Provide --preset or --manifest")

    if not path.is_file():
        raise FileNotFoundError(f"Wave manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return EvalWaveManifest.from_dict(data)
