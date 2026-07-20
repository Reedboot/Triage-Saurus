from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = 60000,
    enable_wal: bool = True,
    enable_foreign_keys: bool = True,
) -> None:
    conn.row_factory = sqlite3.Row
    if enable_foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        pass
    if enable_wal:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass


def open_sqlite_connection(
    db_path: Path,
    *,
    timeout: int = 30,
    retries: int = 6,
    retry_delay: float = 0.5,
    busy_timeout_ms: int = 60000,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    last_error: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=timeout)
            configure_sqlite_connection(conn, busy_timeout_ms=busy_timeout_ms)
            return conn
        except sqlite3.OperationalError as exc:
            last_error = exc
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if "locked" not in str(exc).lower() or attempt >= retries - 1:
                raise
            time.sleep(min(retry_delay * (2**attempt), 5.0))

    if last_error is not None:
        raise last_error
    raise sqlite3.OperationalError("failed to open sqlite connection")
