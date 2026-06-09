"""
SQLite runtime helpers for long-lived bot / worker processes.

Minimal hardening: WAL journal, busy timeout, foreign keys.
Does not change schema or persistence semantics.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    """Apply pragmas safe for concurrent readers + short write transactions."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


def open_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite with row factory and runtime pragmas."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    return conn


def journal_mode(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("PRAGMA journal_mode").fetchone()
    if row is None:
        return None
    return str(row[0])


def ping_database(db_path: str | Path) -> bool:
    """Lightweight readiness probe (open → SELECT 1 → close)."""
    conn = open_sqlite_connection(db_path)
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    finally:
        conn.close()
