#!/usr/bin/env python3
"""Learning database helpers for Triage-Saurus experiments.

Provides create_experiment / update_experiment / get_experiment / print_status
backed by the shared SQLite database at Output/Data/cozo.db.  The ``experiments``
table is created automatically on first use.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "Output" / "Data" / "cozo.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS experiments (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    repos           TEXT DEFAULT '[]',
    version         TEXT,
    status          TEXT DEFAULT 'pending',
    started_at      TEXT,
    completed_at    TEXT,
    promoted_at     TEXT,
    findings_count  INTEGER,
    duration_sec    INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def create_experiment(
    exp_id: str,
    name: str,
    repos: list[str],
    version: str = "default",
) -> None:
    """Insert a new experiment row (ignored if already present)."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO experiments
               (id, name, repos, version, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (exp_id, name, json.dumps(repos), version, datetime.now().isoformat()),
        )


def update_experiment(exp_id: str, **kwargs: Any) -> None:
    """Update arbitrary columns on an experiment row.

    Unknown column names are stored as JSON in a ``meta`` column if present,
    otherwise silently skipped so callers never have to guard this call.
    """
    if not kwargs:
        return

    # Only update columns that actually exist in the table
    known = {
        "name", "repos", "version", "status",
        "started_at", "completed_at", "promoted_at",
        "findings_count", "duration_sec",
    }
    updates = {k: v for k, v in kwargs.items() if k in known}
    if not updates:
        return

    # Serialise list values
    for k, v in updates.items():
        if isinstance(v, (list, dict)):
            updates[k] = json.dumps(v)

    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [exp_id]
    with _connect() as conn:
        conn.execute(f"UPDATE experiments SET {cols} WHERE id = ?", vals)


def get_experiment(exp_id: str) -> Optional[dict]:
    """Return experiment row as a dict, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (exp_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["repos"] = json.loads(d.get("repos") or "[]")
    except (ValueError, TypeError):
        pass
    return d


def print_status() -> None:
    """Print a summary table of all experiments."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, status, findings_count, created_at FROM experiments ORDER BY id DESC LIMIT 20"
        ).fetchall()

    if not rows:
        print("No experiments recorded.")
        return

    print(f"{'ID':<6} {'Name':<28} {'Status':<14} {'Findings':<10} {'Created'}")
    print("─" * 72)
    for r in rows:
        print(
            f"{r['id']:<6} {(r['name'] or '')[:27]:<28} {(r['status'] or ''):<14}"
            f" {(r['findings_count'] or 0):<10} {(r['created_at'] or '')[:19]}"
        )
