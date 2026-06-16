"""Filesystem-safe path segment helpers (Windows-compatible)."""

from __future__ import annotations

import re

_WINDOWS_INVALID_RE = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f]")


def filesystem_safe_segment(
    value: str,
    *,
    max_len: int = 120,
    replacement: str = "-",
) -> str:
    """
    Sanitize a logical key for use as a single directory/file name segment.

    Does not alter the logical match_key used in runtime contracts — apply only
    when building filesystem paths.
    """
    text = (value or "").strip()
    if not text:
        return "unknown"

    text = _CONTROL_CHARS_RE.sub(replacement, text)
    text = _WINDOWS_INVALID_RE.sub(replacement, text)

    if replacement:
        doubled = replacement * 2
        while doubled in text:
            text = text.replace(doubled, replacement)
        text = text.strip(f" {replacement}.")

    if not text:
        text = "unknown"

    return text[:max_len]
