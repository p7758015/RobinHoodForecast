"""
Canonical filesystem paths for the football_agent package.

All runtime data (SQLite, snapshots) and API cache live under the package root,
not the repository CWD. This avoids duplicate `data/` folders when scripts are
run from different working directories.
"""

from pathlib import Path

# football_agent/ (package root)
PACKAGE_ROOT = Path(__file__).resolve().parent

# Runtime data (SQLite, snapshots) — canonical location per original project layout
DATA_DIR = PACKAGE_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
# Debug/live Flashscore fixture exports (CLI ``flashscore_trace``)
FLASHSCORE_FIXTURES_DEBUG_DIR = DATA_DIR / "flashscore_fixtures"

# API response cache (md5 JSON files)
CACHE_DIR = PACKAGE_ROOT / "cache"

DEFAULT_DB_FILENAME = "football_agent.db"
DEFAULT_DB_PATH = DATA_DIR / DEFAULT_DB_FILENAME


def ensure_runtime_dirs() -> None:
    """Create runtime directories if missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
