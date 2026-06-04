#!/usr/bin/env python3
"""Build a full APIM → Backend routing map and persist it to cozo.db.

For each APIM instance in the subscription this script:
  1. Lists every API and records its path + backend service URL
  2. Lists every backend entity and records its URL + circuit-breaker config
  3. Cross-references API serviceUrl / named-backend with provisioned_assets.fqdn
     to create resource_connections rows (type='apim_routing')
  4. Stores the raw API→backend mapping in a new apim_api_routes table
  5. Can also be imported by the main harvest pipeline to derive backend rows
     from those routes without making extra Azure CLI calls

Usage:
    python Scripts/Harvest/apim_routing_map.py --subscription "mysub"
    python Scripts/Harvest/apim_routing_map.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore

# ---------------------------------------------------------------------------
# Schema extension — apim_api_routes table
# ---------------------------------------------------------------------------

_APIM_ROUTES_DDL = """
CREATE TABLE IF NOT EXISTS apim_api_routes (
    id                  TEXT PRIMARY KEY,   -- {apim_name}::{api_name}
    subscription_id     TEXT NOT NULL,
    apim_name           TEXT NOT NULL,
    apim_resource_id    TEXT,
    api_name            TEXT NOT NULL,
    api_display_name    TEXT,
    api_path            TEXT,
    api_protocols       TEXT,               -- JSON array
    backend_id          TEXT,               -- named backend entity id, if resolved
    backend_url         TEXT,               -- resolved backend URL
    service_url         TEXT,               -- serviceUrl on the API itself (may differ from backend)
    requires_subscription INTEGER DEFAULT 1,
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_apim_routes_sub  ON apim_api_routes(subscription_id);
CREATE INDEX IF NOT EXISTS idx_apim_routes_apim ON apim_api_routes(apim_name);

CREATE TABLE IF NOT EXISTS apim_api_operations (
    id                  TEXT PRIMARY KEY,   -- {apim_name}::{api_name}::{operation_id}
    subscription_id     TEXT NOT NULL,
    apim_name           TEXT NOT NULL,
    api_name            TEXT NOT NULL,
    api_display_name    TEXT,
    api_path            TEXT,               -- base path of the owning API
    backend_url         TEXT,               -- inherited from API-level route
    operation_id        TEXT NOT NULL,
    display_name        TEXT,
    method              TEXT,               -- GET POST PUT DELETE PATCH etc.
    url_template        TEXT,               -- e.g. /users/{userId}
    description         TEXT,
    requires_subscription INTEGER DEFAULT 1,
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_apim_ops_sub  ON apim_api_operations(subscription_id);
CREATE INDEX IF NOT EXISTS idx_apim_ops_apim ON apim_api_operations(apim_name);
CREATE INDEX IF NOT EXISTS idx_apim_ops_api  ON apim_api_operations(api_name);

CREATE TABLE IF NOT EXISTS apim_backends (
    id                  TEXT PRIMARY KEY,   -- {apim_name}::{backend_id}
    subscription_id     TEXT NOT NULL,
    apim_name           TEXT NOT NULL,
    backend_id          TEXT NOT NULL,
    title               TEXT,
    description         TEXT,
    url                 TEXT,
    protocol            TEXT,               -- http | soap
    circuit_breaker     TEXT,               -- JSON
    credentials         TEXT,               -- JSON (headers, query params, cert)
    tls_validate_cert   INTEGER DEFAULT 1,
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_apim_backends_sub  ON apim_backends(subscription_id);
CREATE INDEX IF NOT EXISTS idx_apim_backends_apim ON apim_backends(apim_name);
"""


def _ensure_apim_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_APIM_ROUTES_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------

def _az(*args: str, subscription_id: str) -> Any:
    cmd = ["az", *args, "--subscription", subscription_id, "--output", "json"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        print(f"    [warn] az {' '.join(args[:3])} timed out after 120s; skipping")
        return []

    if proc.returncode != 0:
        print(f"    [warn] az {' '.join(args[:3])} failed: {stderr.strip()[:120]}")
        return []
    try:
        return json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        preview = (stdout or "").replace("\n", " ")[:200]
        print(
            f"    [warn] az {' '.join(args[:3])} returned invalid JSON: {exc.msg}; "
            f"output={preview!r}"
        )
        return []


def list_apim_instances(subscription_id: str) -> list[dict]:
    return _az("apim", "list", subscription_id=subscription_id)


def list_apis(apim_name: str, resource_group: str, subscription_id: str) -> list[dict]:
    return _az(
        "apim", "api", "list",
        "--service-name", apim_name,
        "-g", resource_group,
        subscription_id=subscription_id,
    )


def list_backends(apim_name: str, resource_group: str, subscription_id: str) -> list[dict]:
    return _az(
        "apim", "backend", "list",
        "--service-name", apim_name,
        "-g", resource_group,
        subscription_id=subscription_id,
    )


def list_operations(apim_name: str, resource_group: str, api_id: str, subscription_id: str) -> list[dict]:
    return _az(
        "apim", "api", "operation", "list",
        "--service-name", apim_name,
        "-g", resource_group,
        "--api-id", api_id,
        subscription_id=subscription_id,
    )


# ---------------------------------------------------------------------------
# FQDN extraction helpers
# ---------------------------------------------------------------------------

def _url_to_fqdn(url: str | None) -> str | None:
    """Strip scheme, path and port from a URL to get just the hostname."""
    if not url:
        return None
    host = url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    return host or None


def _is_fabric_url(url: str | None) -> bool:
    return bool(url and url.startswith("fabric:/"))


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_apim(
    apim: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool,
) -> int:
    started = time.perf_counter()
    apim_name = apim["name"]
    resource_group = apim["resourceGroup"]
    apim_resource_id = apim.get("id")
    now = datetime.now(timezone.utc).isoformat()
    experiment_id = f"harvest-{subscription_id}"

    print(f"\n  [apim] {apim_name} (rg={resource_group})")

    # --- Backends ---
    backends_started = time.perf_counter()
    print(f"    fetching backends...", end=" ", flush=True)
    backends_raw = list_backends(apim_name, resource_group, subscription_id)
    # Build lookup: backend name → URL
    backend_map: dict[str, str] = {}
    for b in backends_raw:
        b_name = b.get("name") or ""
        b_url = b.get("url") or ""
        if b_url:
            backend_map[b_name] = b_url
    print(f"{len(backends_raw)} backends in {time.perf_counter() - backends_started:.2f}s")

    # Persist full backend details
    if not dry_run:
        for b in backends_raw:
            b_name = b.get("name") or ""
            props = b.get("properties") or b
            cb_raw = props.get("circuitBreaker") or b.get("circuitBreaker")
            cred_raw = props.get("credentials") or b.get("credentials")
            tls_raw = props.get("tls") or b.get("tls") or {}
            conn.execute(
                """
                INSERT INTO apim_backends
                    (id, subscription_id, apim_name, backend_id, title, description,
                     url, protocol, circuit_breaker, credentials, tls_validate_cert, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, description=excluded.description,
                    url=excluded.url, protocol=excluded.protocol,
                    circuit_breaker=excluded.circuit_breaker,
                    credentials=excluded.credentials,
                    tls_validate_cert=excluded.tls_validate_cert,
                    last_synced=excluded.last_synced
                """,
                (
                    f"{apim_name}::{b_name}",
                    subscription_id, apim_name, b_name,
                    props.get("title") or b.get("title"),
                    props.get("description") or b.get("description"),
                    props.get("url") or b.get("url"),
                    props.get("protocol") or b.get("protocol") or "http",
                    json.dumps(cb_raw) if cb_raw else None,
                    json.dumps(cred_raw) if cred_raw else None,
                    1 if tls_raw.get("validateCertificateChain", True) else 0,
                    now,
                ),
            )

    # --- APIs ---
    apis_started = time.perf_counter()
    print(f"    fetching APIs...", end=" ", flush=True)
    apis_raw = list_apis(apim_name, resource_group, subscription_id)
    print(f"{len(apis_raw)} APIs in {time.perf_counter() - apis_started:.2f}s")

    # --- Load existing provisioned_assets FQDNs for cross-reference ---
    asset_fqdn_rows = conn.execute(
        "SELECT id, name, type, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
        (subscription_id,),
    ).fetchall()
    fqdn_to_asset: dict[str, tuple[str, str, str | None]] = {
        row[3]: (row[0], row[1], row[2]) for row in asset_fqdn_rows if row[3]
    }

    routes_upserted = 0
    connections_created = 0
    connections_skipped = 0
    api_jobs: list[dict[str, Any]] = []

    apim_asset_rows = conn.execute(
        "SELECT type FROM provisioned_assets WHERE subscription_id = ? AND name = ?",
        (subscription_id, apim_name),
    ).fetchall()
    apim_asset_type = apim_asset_rows[0][0] if apim_asset_rows else apim.get("type") or "Microsoft.ApiManagement/service"

    def _lookup_resource_id(resource_name: str, resource_type: str | None = None) -> int | None:
        if not resource_name:
            return None
        if resource_type:
            row = conn.execute(
                """
                SELECT id FROM resources
                WHERE experiment_id = ? AND resource_name = ? AND resource_type = ?
                LIMIT 1
                """,
                (experiment_id, resource_name, resource_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id FROM resources
                WHERE experiment_id = ? AND resource_name = ?
                LIMIT 1
                """,
                (experiment_id, resource_name),
            ).fetchone()
        return row[0] if row else None

    for api in apis_raw:
        api_name = api.get("name") or ""
        api_path = api.get("path") or ""
        api_display = api.get("displayName") or api_name
        service_url = api.get("serviceUrl") or ""
        protocols = json.dumps(api.get("protocols") or [])
        requires_sub = 1 if api.get("subscriptionRequired", True) else 0

        # Resolve backend URL: prefer serviceUrl, fall back to named backend lookup
        backend_url = service_url or None
        backend_id: str | None = None

        # Try to find a named backend whose URL matches the service_url
        for bname, burl in backend_map.items():
            if burl and service_url and _url_to_fqdn(burl) == _url_to_fqdn(service_url):
                backend_id = bname
                break

        route_id = f"{apim_name}::{api_name}"

        if not dry_run:
            conn.execute(
                """
                INSERT INTO apim_api_routes
                    (id, subscription_id, apim_name, apim_resource_id,
                     api_name, api_display_name, api_path, api_protocols,
                     backend_id, backend_url, service_url,
                     requires_subscription, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    api_display_name    = excluded.api_display_name,
                    api_path            = excluded.api_path,
                    api_protocols       = excluded.api_protocols,
                    backend_id          = excluded.backend_id,
                    backend_url         = excluded.backend_url,
                    service_url         = excluded.service_url,
                    requires_subscription = excluded.requires_subscription,
                    last_synced         = excluded.last_synced
                """,
                (
                    route_id, subscription_id, apim_name, apim_resource_id,
                    api_name, api_display, api_path, protocols,
                    backend_id, backend_url, service_url,
                    requires_sub, now,
                ),
            )
            routes_upserted += 1
            api_jobs.append({
                "api_name": api_name,
                "api_display": api_display,
                "api_path": api_path,
                "backend_url": backend_url,
                "requires_sub": requires_sub,
            })

            # --- Create resource_connection: APIM → backend asset ---
            backend_fqdn = _url_to_fqdn(backend_url)
            if backend_fqdn and not _is_fabric_url(backend_url):
                # Find asset by FQDN (exact or suffix match)
                target_asset_name: str | None = None
                target_asset_type: str | None = None
                for fqdn, (_asset_id, asset_name, asset_type) in fqdn_to_asset.items():
                    if fqdn == backend_fqdn or backend_fqdn.endswith(f".{fqdn}") or fqdn.endswith(f".{backend_fqdn}"):
                        target_asset_name = asset_name
                        target_asset_type = asset_type
                        break

                source_resource_id = _lookup_resource_id(apim_name, apim_asset_type)
                if source_resource_id is None:
                    connections_skipped += 1
                    continue

                target_resource_id = _lookup_resource_id(target_asset_name or "", target_asset_type) if target_asset_name else None
                conn.execute(
                    """
                    INSERT OR REPLACE INTO resource_connections
                        (experiment_id, source_resource_id, target_resource_id, connection_type,
                         target_external, connection_metadata)
                    VALUES (?, ?, ?, 'apim_routing', ?, ?)
                    """,
                    (
                        experiment_id,
                        source_resource_id,
                        target_resource_id,
                        None if target_resource_id else backend_fqdn or backend_url,
                        json.dumps({
                            "api_name": api_name,
                            "api_path": api_path,
                            "backend_url": backend_url,
                            "requires_subscription": bool(requires_sub),
                        }),
                    ),
                )
                connections_created += 1

    api_operation_results: dict[str, list[dict[str, Any]]] = {}
    if not dry_run and api_jobs:
        ops_started = time.perf_counter()
        print(f"    [apim] fetching API operation lists in parallel for {len(api_jobs)} APIs...", flush=True)
        with ThreadPoolExecutor(max_workers=min(8, len(api_jobs))) as pool:
            futures = {
                pool.submit(list_operations, apim_name, resource_group, job["api_name"], subscription_id): job
                for job in api_jobs
            }
            completed = 0
            total_jobs = len(futures)
            for future in as_completed(futures):
                completed += 1
                job = futures[future]
                try:
                    api_operation_results[job["api_name"]] = future.result() or []
                except Exception as exc:
                    print(f"      {job['api_display']}: FAILED ({exc})")
                    api_operation_results[job["api_name"]] = []
                if completed == 1 or completed % 5 == 0 or completed == total_jobs:
                    print(f"    [apim] {apim_name}: {completed}/{total_jobs} API operation lists fetched", flush=True)
        print(f"    [apim] API operation phase completed in {time.perf_counter() - ops_started:.2f}s", flush=True)

        for job in api_jobs:
            ops_raw = api_operation_results.get(job["api_name"], [])
            ops_upserted = 0
            for op in ops_raw:
                op_id = op.get("name") or op.get("id", "").split("/")[-1] or ""
                op_disp = op.get("displayName") or op_id
                method = op.get("method") or ""
                url_tpl = op.get("urlTemplate") or op.get("url") or ""
                desc = op.get("description") or ""
                conn.execute(
                    """
                    INSERT INTO apim_api_operations
                        (id, subscription_id, apim_name, api_name, api_display_name,
                         api_path, backend_url, operation_id, display_name,
                         method, url_template, description, requires_subscription, last_synced)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        display_name=excluded.display_name,
                        method=excluded.method,
                        url_template=excluded.url_template,
                        description=excluded.description,
                        api_path=excluded.api_path,
                        backend_url=excluded.backend_url,
                        requires_subscription=excluded.requires_subscription,
                        last_synced=excluded.last_synced
                    """,
                    (
                        f"{apim_name}::{job['api_name']}::{op_id}",
                        subscription_id,
                        apim_name,
                        job["api_name"],
                        job["api_display"],
                        job["api_path"],
                        job["backend_url"],
                        op_id,
                        op_disp,
                        method.upper(),
                        url_tpl,
                        desc,
                        job["requires_sub"],
                        now,
                    ),
                )
                ops_upserted += 1
            if ops_raw:
                print(f"      {job['api_display']}: {ops_upserted} operations")

    if not dry_run:
        conn.commit()

    if connections_skipped:
        print(f"    [warn] skipped {connections_skipped} connection rows because the numeric resource graph is unavailable")
    print(f"    → {routes_upserted} routes upserted, {connections_created} connections created")
    print(f"    [apim-routing] {apim_name} finished in {time.perf_counter() - started:.2f}s", flush=True)
    return routes_upserted


def harvest_backends(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Derive APIM backend rows from already harvested API routes.

    This fast sub-step avoids extra Azure CLI calls. It reads apim_api_routes
    and materialises a backend row for each unique backend URL per APIM.
    """
    started = time.perf_counter()
    _ensure_schema(conn)
    _ensure_apim_schema(conn)

    route_rows = conn.execute(
        """
        SELECT apim_name, api_name, backend_id, backend_url, service_url,
               requires_subscription, last_synced
        FROM apim_api_routes
        WHERE subscription_id = ?
        ORDER BY apim_name, api_name
        """,
        (subscription_id,),
    ).fetchall()
    if not route_rows:
        print("    [apim-backends] no APIM routes harvested yet")
        print(f"    [apim-backends] finished in {time.perf_counter() - started:.2f}s", flush=True)
        return 0, 0

    now = datetime.now(timezone.utc).isoformat()
    def _backend_key(url: str | None) -> str | None:
        if not url:
            return None
        value = url.strip()
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme:
            host = (parsed.netloc or parsed.path).lower().rstrip("/")
            path = parsed.path.strip("/").lower()
            return f"{host}/{path}" if path else host or None
        return value.rstrip("/").lower() or None

    backend_rows: dict[tuple[str, str], dict[str, Any]] = {}
    linked_routes = 0

    for apim_name, api_name, backend_id, backend_url, service_url, requires_subscription, last_synced in route_rows:
        resolved_url = (backend_url or service_url or "").strip()
        if not resolved_url:
            continue
        linked_routes += 1
        backend_key = _backend_key(backend_id or resolved_url)
        if not backend_key:
            continue
        row_key = (apim_name, backend_key)
        if row_key in backend_rows:
            continue

        protocol = urlparse(resolved_url).scheme or ("https" if resolved_url.startswith("https://") else "http")
        backend_rows[row_key] = {
            "id": f"{apim_name}::{backend_key}",
            "subscription_id": subscription_id,
            "apim_name": apim_name,
            "backend_id": backend_key,
            "title": _url_to_fqdn(resolved_url) or backend_key,
            "description": f"Derived from APIM API {api_name}",
            "url": resolved_url,
            "protocol": protocol,
            "circuit_breaker": None,
            "credentials": None,
            "tls_validate_cert": 1 if protocol == "https" else 0,
            "last_synced": last_synced or now,
        }

    if not dry_run:
        conn.execute(
            "DELETE FROM apim_backends WHERE subscription_id = ?",
            (subscription_id,),
        )
        for backend in backend_rows.values():
            conn.execute(
                """
                INSERT INTO apim_backends
                    (id, subscription_id, apim_name, backend_id, title, description,
                     url, protocol, circuit_breaker, credentials, tls_validate_cert, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    url=excluded.url,
                    protocol=excluded.protocol,
                    circuit_breaker=excluded.circuit_breaker,
                    credentials=excluded.credentials,
                    tls_validate_cert=excluded.tls_validate_cert,
                    last_synced=excluded.last_synced
                """,
                (
                    backend["id"],
                    backend["subscription_id"],
                    backend["apim_name"],
                    backend["backend_id"],
                    backend["title"],
                    backend["description"],
                    backend["url"],
                    backend["protocol"],
                    backend["circuit_breaker"],
                    backend["credentials"],
                    backend["tls_validate_cert"],
                    backend["last_synced"],
                ),
            )
        conn.commit()

    print(f"    [apim-backends] derived {len(backend_rows)} backends from {linked_routes} routes")
    if not backend_rows:
        print("    [apim-backends] no backend URLs were available to derive")
    print(f"    [apim-backends] finished in {time.perf_counter() - started:.2f}s", flush=True)

    return len(backend_rows), linked_routes


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build APIM → backend routing map from live Azure subscription"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID")
    group.add_argument("--all", action="store_true", dest="all_subs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Resolve subscriptions
    subs_raw = subprocess.run(
        ["az", "account", "list", "--output", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if subs_raw.returncode != 0:
        raise RuntimeError(f"az account list failed: {subs_raw.stderr.strip()[:200]}")
    try:
        all_subs: list[dict] = json.loads(subs_raw.stdout or "[]")
    except json.JSONDecodeError as exc:
        preview = (subs_raw.stdout or "").replace("\n", " ")[:200]
        raise RuntimeError(f"az account list returned invalid JSON: {exc.msg}; output={preview!r}") from exc

    if args.all_subs:
        target_subs = all_subs
    else:
        needle = args.subscription.lower()
        target_subs = [
            s for s in all_subs
            if s.get("id", "").lower() == needle or s.get("name", "").lower() == needle
        ]
        if not target_subs:
            names = [s.get("name") for s in all_subs]
            print(f"[error] Subscription '{args.subscription}' not found. Available: {names}", file=sys.stderr)
            sys.exit(1)

    db_path = REPO_ROOT / "Output" / "Data" / "cozo.db"
    if not db_path.exists():
        print(f"[error] cozo.db not found at {db_path}. Run harvest_azure_assets.py first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)
    _ensure_apim_schema(conn)

    total = 0
    for sub in target_subs:
        sub_id = sub["id"]
        sub_name = sub.get("name") or sub_id
        print(f"\n[subscription] {sub_name}")

        apim_instances = list_apim_instances(sub_id)
        if not apim_instances:
            print("  No APIM instances found — skipping")
            continue

        for apim in apim_instances:
            total += process_apim(apim, sub_id, conn, args.dry_run)

    conn.close()
    print(f"\n[apim-routing] Done. {total} API routes across {len(target_subs)} subscription(s).")


if __name__ == "__main__":
    main()
