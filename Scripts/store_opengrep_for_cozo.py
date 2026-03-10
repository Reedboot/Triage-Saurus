#!/usr/bin/env python3
"""Store opengrep scan output inside a Cozo DB for later enrichment."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import sqlite3
try:
    from pycozo import Client
    _HAS_PYCOZO = True
except Exception:
    Client = None
    _HAS_PYCOZO = False

DEFAULT_COZO_DB = Path("Output/Data/cozo.db")


def _severity_score(severity: str | None) -> int:
    if not severity:
        return 4
    value = severity.upper()
    if value == "ERROR":
        return 8
    if value == "WARNING":
        return 5
    if value == "INFO":
        return 2
    return 4


def _detect_provider(check_id: str, metadata: Mapping[str, Any] | None) -> str:
    metadata = metadata or {}
    tech = metadata.get("technology") or metadata.get("technologies")
    if isinstance(tech, str):
        tech_values = [tech.lower()]
    elif isinstance(tech, Iterable):
        tech_values = [str(item).lower() for item in tech if item is not None]
    else:
        tech_values = []

    for token in tech_values:
        if "azure" in token or "azurerm" in token:
            return "azure"
        if "aws" in token or "amazon" in token:
            return "aws"
        if "gcp" in token or "google" in token:
            return "gcp"
        if "terraform" in token:
            return "terraform"

    lower_id = check_id.lower()
    if "azure" in lower_id or "azurerm" in lower_id:
        return "azure"
    if "aws" in lower_id:
        return "aws"
    if "gcp" in lower_id or "google" in lower_id:
        return "gcp"
    if "terraform" in lower_id:
        return "terraform"
    return "unknown"


def _make_finding_id(repo_name: str, rule_id: str, path: str, start_line: int) -> str:
    payload = f"{repo_name}|{rule_id}|{path}|{start_line}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_context_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _extract_metavar(entry: Mapping[str, Any]) -> str:
    for attr in ("abstract_content", "value", "content"):
        val = entry.get(attr)
        if val:
            return _normalize_context_value(val).strip('"')
    return _normalize_context_value(entry)


def _context_entries(finding_id: str, extra: Mapping[str, Any]) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    metadata = extra.get("metadata") or {}
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            context.append(
                {
                    "finding_id": finding_id,
                    "context_key": f"metadata.{key}",
                    "context_value": _normalize_context_value(value),
                    "context_type": "metadata",
                }
            )

    metavars = extra.get("metavars") or {}
    if isinstance(metavars, Mapping):
        for key, datum in metavars.items():
            if isinstance(datum, Mapping):
                value = _extract_metavar(datum)
            else:
                value = _normalize_context_value(datum)
            context.append(
                {
                    "finding_id": finding_id,
                    "context_key": key,
                    "context_value": value,
                    "context_type": "metavar",
                }
            )

    return context


def _ensure_relations(db: Client) -> None:
    existing = {row[0] for row in db.relations()["rows"]}
    schema = {
        "repo_scans": ["scan_id", "repo_name", "repo_path", "scan_time"],
        "findings": [
            "finding_id",
            "scan_id",
            "repo_name",
            "repo_path",
            "rule_id",
            "check_id",
            "category",
            "severity",
            "severity_score",
            "message",
            "code_snippet",
            "metadata_json",
            "provider",
            "source_file",
            "start_line",
            "end_line",
            "created_at",
        ],
        "finding_context": [
            "finding_id",
            "context_key",
            "context_value",
            "context_type",
        ],
    }

    for name, columns in schema.items():
        if name not in existing:
            db.create(name, *columns)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import opengrep JSON scans into Cozo.")
    parser.add_argument("scan_json", type=Path, help="Path to opengrep JSON output.")
    parser.add_argument("--repo", default="unknown", help="Repository name under scan.")
    parser.add_argument(
        "--repo-path",
        type=Path,
        help="Repository path (optional).",
    )
    parser.add_argument(
        "--scan-id",
        help="Optional scan identifier (defaults to a random UUID).",
    )
    args = parser.parse_args()

    if not args.scan_json.exists():
        print(f"ERROR: scan file not found: {args.scan_json}", file=sys.stderr)
        sys.exit(1)

    DEFAULT_COZO_DB.parent.mkdir(parents=True, exist_ok=True)
    with args.scan_json.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    scan_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    scan_id = args.scan_id or hashlib.sha1(scan_time.encode("utf-8")).hexdigest()

    if _HAS_PYCOZO and Client is not None:
        # Preferred path: use pycozo Client to create relations and store.
        db = Client(engine="sqlite", path=str(DEFAULT_COZO_DB), dataframe=False)
        try:
            _ensure_relations(db)

            db.insert(
                "repo_scans",
                {
                    "scan_id": scan_id,
                    "repo_name": args.repo,
                    "repo_path": str(args.repo_path) if args.repo_path else "",
                    "scan_time": scan_time,
                },
            )

            stored = 0
            contexts = 0
            for result in data.get("results", []):
                extra = result.get("extra", {}) or {}
                metadata = extra.get("metadata") or {}
                rule_id = result.get("check_id", "")
                path = result.get("path", "")
                start_line = result.get("start", {}).get("line", 0) or 0
                end_line = result.get("end", {}).get("line", start_line) or start_line
                severity = extra.get("severity", "WARNING")
                finding_id = _make_finding_id(args.repo, rule_id, path, start_line)
                provider = _detect_provider(rule_id, metadata)

                message = (extra.get("message") or "").strip()
                code_snippet = extra.get("lines") or ""
                category = metadata.get("category") if isinstance(metadata, Mapping) else None
                metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)

                db.insert(
                    "findings",
                    {
                        "finding_id": finding_id,
                        "scan_id": scan_id,
                        "repo_name": args.repo,
                        "repo_path": str(args.repo_path) if args.repo_path else "",
                        "rule_id": rule_id,
                        "check_id": rule_id,
                        "category": category or "",
                        "severity": severity,
                        "severity_score": _severity_score(severity),
                        "message": message,
                        "code_snippet": code_snippet,
                        "metadata_json": metadata_json,
                        "provider": provider,
                        "source_file": path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "created_at": scan_time,
                    },
                )

                entries = _context_entries(finding_id, extra)
                for entry in entries:
                    db.put("finding_context", entry)

                stored += 1
                contexts += len(entries)

        finally:
            db.close()

        print(f"Stored {stored} findings with {contexts} context rows into {DEFAULT_COZO_DB}")
    else:
        # Fallback path: write directly into SQLite tables so CI can validate DB contents
        conn = sqlite3.connect(str(DEFAULT_COZO_DB))
        cur = conn.cursor()
        # Ensure minimal tables exist
        cur.execute('''
            CREATE TABLE IF NOT EXISTS repo_scans (
                scan_id TEXT PRIMARY KEY,
                repo_name TEXT,
                repo_path TEXT,
                scan_time TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id TEXT UNIQUE,
                scan_id TEXT,
                repo_name TEXT,
                repo_path TEXT,
                rule_id TEXT,
                check_id TEXT,
                category TEXT,
                severity TEXT,
                severity_score INTEGER,
                message TEXT,
                code_snippet TEXT,
                metadata_json TEXT,
                provider TEXT,
                source_file TEXT,
                start_line INTEGER,
                end_line INTEGER,
                created_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS finding_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id TEXT,
                context_key TEXT,
                context_value TEXT,
                context_type TEXT
            )
        ''')
        conn.commit()

        # Ensure findings table has required columns (backfill missing columns via ALTER TABLE)
        try:
            cur.execute("PRAGMA table_info(findings)")
            rows = cur.fetchall()
            existing_cols = {r[1] for r in rows}
        except Exception:
            existing_cols = set()

        required_columns = {
            "finding_id": "TEXT",
            "scan_id": "TEXT",
            "repo_name": "TEXT",
            "repo_path": "TEXT",
            "rule_id": "TEXT",
            "check_id": "TEXT",
            "category": "TEXT",
            "severity": "TEXT",
            "severity_score": "INTEGER",
            "message": "TEXT",
            "code_snippet": "TEXT",
            "metadata_json": "TEXT",
            "provider": "TEXT",
            "source_file": "TEXT",
            "start_line": "INTEGER",
            "end_line": "INTEGER",
            "created_at": "TEXT",
        }

        for col, col_type in required_columns.items():
            if col not in existing_cols:
                try:
                    cur.execute(f"ALTER TABLE findings ADD COLUMN {col} {col_type}")
                except Exception:
                    # If ALTER fails (e.g., read-only or incompatible schema), continue
                    pass
        conn.commit()

        # Ensure repositories and resources tables exist for CI quality gates
        cur.execute('''
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT UNIQUE,
                repo_path TEXT,
                created_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id TEXT UNIQUE,
                repo_name TEXT,
                resource_type TEXT,
                resource_name TEXT,
                provider TEXT,
                properties_json TEXT,
                created_at TEXT
            )
        ''')
        conn.commit()

        # Insert repository record
        try:
            cur.execute(
                "INSERT OR IGNORE INTO repositories (repo_name, repo_path, created_at) VALUES (?, ?, ?)",
                (args.repo, str(args.repo_path) if args.repo_path else "", scan_time),
            )
        except Exception:
            pass

        # Derive and insert resource rows from opengrep metadata extracts when available
        try:
            existing_resources = set()
            for result in data.get("results", []):
                extra = result.get("extra", {}) or {}
                metadata = extra.get("metadata") or {}
                extracts = metadata.get("extracts") or {}
                resource_type = extracts.get("resource_type") if isinstance(extracts, dict) else None
                resource_name = extracts.get("resource_name") if isinstance(extracts, dict) else None
                provider = _detect_provider(result.get("check_id", ""), metadata)
                if resource_type and resource_name:
                    rid_payload = f"{args.repo}|{resource_type}|{resource_name}"
                    resource_id = hashlib.sha1(rid_payload.encode("utf-8")).hexdigest()
                    if resource_id in existing_resources:
                        continue
                    properties_json = json.dumps(extracts.get("properties") or {}, ensure_ascii=False, sort_keys=True)
                    try:
                        cur.execute(
                            "INSERT OR IGNORE INTO resources (resource_id, repo_name, resource_type, resource_name, provider, properties_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                resource_id,
                                args.repo,
                                resource_type,
                                resource_name,
                                provider,
                                properties_json,
                                scan_time,
                            ),
                        )
                        existing_resources.add(resource_id)
                    except Exception:
                        # best-effort: continue on insert failures
                        pass
            conn.commit()
        except Exception:
            # Ignore extraction errors; the main goal is to ensure tables exist and repo row is present
            conn.rollback()

        cur.execute(
            "INSERT OR IGNORE INTO repo_scans (scan_id, repo_name, repo_path, scan_time) VALUES (?, ?, ?, ?)",
            (scan_id, args.repo, str(args.repo_path) if args.repo_path else "", scan_time),
        )

        stored = 0
        contexts = 0
        for result in data.get("results", []):
            extra = result.get("extra", {}) or {}
            metadata = extra.get("metadata") or {}
            rule_id = result.get("check_id", "")
            path = result.get("path", "")
            start_line = result.get("start", {}).get("line", 0) or 0
            end_line = result.get("end", {}).get("line", start_line) or start_line
            severity = extra.get("severity", "WARNING")
            finding_id = _make_finding_id(args.repo, rule_id, path, start_line)
            provider = _detect_provider(rule_id, metadata)

            message = (extra.get("message") or "").strip()
            code_snippet = extra.get("lines") or ""
            category = metadata.get("category") if isinstance(metadata, Mapping) else None
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)

            cur.execute(
                "INSERT OR IGNORE INTO findings (finding_id, scan_id, repo_name, repo_path, rule_id, check_id, category, severity, severity_score, message, code_snippet, metadata_json, provider, source_file, start_line, end_line, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding_id,
                    scan_id,
                    args.repo,
                    str(args.repo_path) if args.repo_path else "",
                    rule_id,
                    rule_id,
                    category or "",
                    severity,
                    _severity_score(severity),
                    message,
                    code_snippet,
                    metadata_json,
                    provider,
                    path,
                    start_line,
                    end_line,
                    scan_time,
                ),
            )

            entries = _context_entries(finding_id, extra)
            for entry in entries:
                cur.execute(
                    "INSERT INTO finding_context (finding_id, context_key, context_value, context_type) VALUES (?, ?, ?, ?)",
                    (entry["finding_id"], entry["context_key"], entry["context_value"], entry["context_type"]),
                )
                contexts += 1

            stored += 1

        conn.commit()
        conn.close()

        print(f"Fallback stored {stored} findings with {contexts} context rows into {DEFAULT_COZO_DB}")


if __name__ == "__main__":
    main()
