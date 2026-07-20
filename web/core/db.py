from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_DB_PATH: Path | None = None
_SCHEMA_CACHE_LOCK = threading.Lock()
_SCHEMA_CACHE: dict[int, dict[str, object]] = {}


def configure_db_path(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path


def _get_db() -> sqlite3.Connection | None:
    """Return a sqlite3.Connection to the learning DB, or None if unavailable."""
    if _DB_PATH is None or not _DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _get_db_with_schema() -> sqlite3.Connection | None:
    """Like _get_db() but ensures the full schema (including harvest tables) exists."""
    if _DB_PATH is None or not _DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        from Scripts.Persist import db_helpers

        db_helpers._ensure_schema(conn)
        return conn
    except Exception:
        return None


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cache = _schema_cache_for_conn(conn)
    exists_cache = cache.setdefault("exists", {})
    if isinstance(exists_cache, dict) and table_name in exists_cache:
        return bool(exists_cache[table_name])
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        exists = bool(row)
        if isinstance(exists_cache, dict):
            exists_cache[table_name] = exists
        return exists
    except Exception:
        return False


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cache = _schema_cache_for_conn(conn)
    columns_cache = cache.setdefault("columns", {})
    if isinstance(columns_cache, dict) and table_name in columns_cache:
        cached_cols = columns_cache[table_name]
        if isinstance(cached_cols, set):
            return set(cached_cols)
    if not table_exists(conn, table_name):
        return set()
    try:
        escaped_table = table_name.replace('"', '""')
        rows = conn.execute(f'PRAGMA table_info("{escaped_table}")').fetchall()
        cols = {str(r[1]) for r in rows}
        if isinstance(columns_cache, dict):
            columns_cache[table_name] = cols
        return cols
    except Exception:
        return set()


def _schema_cache_for_conn(conn: sqlite3.Connection) -> dict[str, object]:
    conn_id = id(conn)
    with _SCHEMA_CACHE_LOCK:
        entry = _SCHEMA_CACHE.get(conn_id)
        if entry is not None and entry.get("conn") is conn:
            return entry
        if len(_SCHEMA_CACHE) >= 256:
            _SCHEMA_CACHE.clear()
        entry = {"conn": conn, "exists": {}, "columns": {}}
        _SCHEMA_CACHE[conn_id] = entry
        return entry
