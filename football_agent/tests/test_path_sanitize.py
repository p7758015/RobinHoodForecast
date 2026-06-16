"""Filesystem path sanitize tests."""

from __future__ import annotations

import json
from pathlib import Path

from football_agent.collectors.flashscore.client import FlashscoreCollectorClient
from football_agent.path_sanitize import filesystem_safe_segment


def test_filesystem_safe_segment_replaces_windows_invalid_chars() -> None:
    raw = 'lfPep2u1:Dziugas Telsiai:FA Siauliai'
    safe = filesystem_safe_segment(raw)
    assert ":" not in safe
    assert safe == "lfPep2u1-Dziugas Telsiai-FA Siauliai"


def test_filesystem_safe_segment_handles_all_reserved_chars() -> None:
    raw = 'a<b>c:d"e/f\\g|h?i*j'
    safe = filesystem_safe_segment(raw)
    for ch in '<>:"/\\|?*':
        assert ch not in safe


def test_collector_client_save_raw_uses_safe_path(tmp_path: Path) -> None:
    client = FlashscoreCollectorClient(
        "http://localhost:3000",
        raw_store_dir=tmp_path,
    )
    match_key = "lfPep2u1:Dziugas Telsiai:FA Siauliai"
    ref = client._save_raw(match_key, "match", {"match_id": "lfPep2u1"})
    path = Path(ref)
    assert path.is_file()
    assert ":" not in str(path.relative_to(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["match_id"] == "lfPep2u1"
