"""SQLite runtime hardening tests."""

from __future__ import annotations

from football_agent.storage.sqlite_runtime import journal_mode, open_sqlite_connection, ping_database


def test_open_sqlite_connection_uses_wal(tmp_path) -> None:
    db = tmp_path / "test.db"
    conn = open_sqlite_connection(db)
    try:
        assert journal_mode(conn) == "wal"
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    assert ping_database(db) is True
