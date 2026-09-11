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
import re
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

_APIM_FETCH_WORKERS = 8

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


def get_api_policy(apim_name: str, resource_group: str, api_id: str, subscription_id: str) -> str:
    policy_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.ApiManagement"
        f"/service/{apim_name}/apis/{api_id}/policies?api-version=2022-08-01"
    )
    policy_response = _az("rest", "--method", "get", "--url", policy_url, subscription_id=subscription_id)
    policy = (
        (policy_response.get("value") or [{}])[0]
        if isinstance(policy_response, dict)
        else {}
    )
    if isinstance(policy, dict):
        return str(policy.get("properties", {}).get("value") or policy.get("value") or "")
    return ""


def _policy_backend_id(policy: str) -> str | None:
    match = re.search(r"<set-backend-service\b[^>]*\bbackend-id=[\"']([^\"']+)", policy or "", re.IGNORECASE)
    return match.group(1).strip() if match else None


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

def collect_apim(
    apim: dict,
    subscription_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Collect an APIM routing bundle without touching SQLite."""
    apim_name = apim["name"]
    resource_group = apim["resourceGroup"]
    print(f"\n  [apim] {apim_name} (rg={resource_group})")

    backends_started = time.perf_counter()
    print(f"    fetching backends and APIs...", end=" ", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        backends_future = pool.submit(list_backends, apim_name, resource_group, subscription_id)
        apis_future = pool.submit(list_apis, apim_name, resource_group, subscription_id)
        backends_raw = backends_future.result()
        apis_raw = apis_future.result()
    # Build lookup: backend name → URL
    backend_map: dict[str, str] = {}
    for b in backends_raw:
        b_name = b.get("name") or ""
        b_url = b.get("url") or ""
        if b_url:
            backend_map[b_name] = b_url
    print(
        f"{len(backends_raw)} backends and {len(apis_raw)} APIs in "
        f"{time.perf_counter() - backends_started:.2f}s"
    )

    api_specs: list[dict[str, Any]] = []
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

        api_specs.append({
            "api_name": api_name,
            "api_display": api_display,
            "api_path": api_path,
            "protocols": protocols,
            "requires_sub": requires_sub,
            "service_url": service_url,
            "backend_id": backend_id,
            "backend_url": backend_url,
        })

    policy_specs = [spec for spec in api_specs if not spec["backend_id"]]
    policy_results: dict[str, str] = {}
    api_operation_results: dict[str, list[dict[str, Any]]] = {}
    operation_specs = api_specs if not dry_run else []
    futures: dict[Any, tuple[str, str]] = {}
    operations_started = time.perf_counter() if operation_specs else None
    if policy_specs or operation_specs:
        # Policies and operation lists are independent Azure reads. Start both
        # kinds of request together so slow policy REST calls do not serialize
        # the operation-list phase.
        max_workers = min(
            _APIM_FETCH_WORKERS,
            max(len(policy_specs) + len(operation_specs), 1),
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for spec in policy_specs:
                futures[pool.submit(
                    get_api_policy,
                    apim_name,
                    resource_group,
                    spec["api_name"],
                    subscription_id,
                )] = ("policy", spec["api_name"])
            for spec in operation_specs:
                futures[pool.submit(
                    list_operations,
                    apim_name,
                    resource_group,
                    spec["api_name"],
                    subscription_id,
                )] = ("operations", spec["api_name"])
            for future in as_completed(futures):
                kind, api_name = futures[future]
                try:
                    value = future.result()
                except Exception as exc:
                    print(f"      {api_name}: FAILED ({exc})")
                    value = [] if kind == "operations" else ""
                if kind == "policy":
                    policy_results[api_name] = value or ""
                else:
                    api_operation_results[api_name] = value or []

    for spec in policy_specs:
        backend_id = _policy_backend_id(policy_results.get(spec["api_name"], ""))
        spec["backend_id"] = backend_id
        if backend_id:
            spec["backend_url"] = backend_map.get(backend_id) or spec["backend_url"]

    if operation_specs:
        print(
            f"    [apim] fetched API operation lists in parallel for "
            f"{len(operation_specs)} APIs in "
            f"{time.perf_counter() - (operations_started or time.perf_counter()):.2f}s",
            flush=True,
        )

    return {
        "apim": apim,
        "backends": backends_raw,
        "api_specs": api_specs,
        "operations": api_operation_results,
        "dry_run": dry_run,
    }


def persist_apim(
    subscription_id: str,
    conn: sqlite3.Connection,
    collected: dict[str, Any],
    *,
    dry_run: bool = False,
) -> int:
    """Persist a previously collected APIM routing bundle."""
    started = time.perf_counter()
    apim = collected["apim"]
    apim_name = apim["name"]
    apim_resource_id = apim.get("id")
    now = datetime.now(timezone.utc).isoformat()
    experiment_id = f"harvest-{subscription_id}"
    backends_raw = collected.get("backends") or []
    api_specs = collected.get("api_specs") or []
    api_operation_results = collected.get("operations") or {}

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
                    f"{apim_name}::{b_name}", subscription_id, apim_name, b_name,
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

    asset_rows = conn.execute(
        """
        SELECT rowid, id, name, type, fqdn
        FROM provisioned_assets
        WHERE subscription_id = ?
        """,
        (subscription_id,),
    ).fetchall()
    fqdn_to_asset = {
        row[4]: (row[1], row[2], row[3]) for row in asset_rows if row[4]
    }
    resource_ids: dict[tuple[str, str | None], int] = {}
    for row in asset_rows:
        resource_ids.setdefault((row[2], row[3]), int(row[0]))
        resource_ids.setdefault((row[2], None), int(row[0]))

    apim_asset_type = next(
        (row[3] for row in asset_rows if row[2] == apim_name),
        apim.get("type") or "Microsoft.ApiManagement/service",
    )

    def _lookup_resource_id(resource_name: str, resource_type: str | None = None) -> int | None:
        return resource_ids.get((resource_name, resource_type)) or resource_ids.get((resource_name, None))

    routes_upserted = 0
    connections_created = 0
    connections_skipped = 0
    api_jobs: list[dict[str, Any]] = []
    for spec in api_specs:
        api_name = spec["api_name"]
        backend_url = spec["backend_url"]
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
                    api_display_name=excluded.api_display_name, api_path=excluded.api_path,
                    api_protocols=excluded.api_protocols, backend_id=excluded.backend_id,
                    backend_url=excluded.backend_url, service_url=excluded.service_url,
                    requires_subscription=excluded.requires_subscription,
                    last_synced=excluded.last_synced
                """,
                (
                    f"{apim_name}::{api_name}", subscription_id, apim_name, apim_resource_id,
                    api_name, spec["api_display"], spec["api_path"], spec["protocols"],
                    spec["backend_id"], backend_url, spec["service_url"],
                    spec["requires_sub"], now,
                ),
            )
            routes_upserted += 1
            api_jobs.append({
                "api_name": api_name,
                "api_display": spec["api_display"],
                "api_path": spec["api_path"],
                "backend_url": backend_url,
                "requires_sub": spec["requires_sub"],
            })

            backend_fqdn = _url_to_fqdn(backend_url)
            if backend_fqdn and not _is_fabric_url(backend_url):
                target_asset_name = target_asset_type = None
                for fqdn, (_asset_id, asset_name, asset_type) in fqdn_to_asset.items():
                    if fqdn == backend_fqdn or backend_fqdn.endswith(f".{fqdn}") or fqdn.endswith(f".{backend_fqdn}"):
                        target_asset_name, target_asset_type = asset_name, asset_type
                        break
                source_resource_id = _lookup_resource_id(apim_name, apim_asset_type)
                if source_resource_id is None:
                    connections_skipped += 1
                    continue
                target_resource_id = _lookup_resource_id(target_asset_name or "", target_asset_type)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO resource_connections
                        (experiment_id, source_resource_id, target_resource_id, connection_type,
                         target_external, connection_metadata)
                    VALUES (?, ?, ?, 'apim_routing', ?, ?)
                    """,
                    (
                        experiment_id, source_resource_id, target_resource_id,
                        None if target_resource_id else backend_fqdn or backend_url,
                        json.dumps({
                            "api_name": api_name, "api_path": spec["api_path"],
                            "backend_url": backend_url,
                            "requires_subscription": bool(spec["requires_sub"]),
                        }),
                    ),
                )
                connections_created += 1

    for job in api_jobs:
        ops_raw = api_operation_results.get(job["api_name"], [])
        ops_upserted = 0
        for op in ops_raw:
            op_id = op.get("name") or op.get("id", "").split("/")[-1] or ""
            conn.execute(
                """
                INSERT INTO apim_api_operations
                    (id, subscription_id, apim_name, api_name, api_display_name,
                     api_path, backend_url, operation_id, display_name,
                     method, url_template, description, requires_subscription, last_synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name=excluded.display_name, method=excluded.method,
                    url_template=excluded.url_template, description=excluded.description,
                    api_path=excluded.api_path, backend_url=excluded.backend_url,
                    requires_subscription=excluded.requires_subscription,
                    last_synced=excluded.last_synced
                """,
                (
                    f"{apim_name}::{job['api_name']}::{op_id}", subscription_id, apim_name,
                    job["api_name"], job["api_display"], job["api_path"], job["backend_url"],
                    op_id, op.get("displayName") or op_id, (op.get("method") or "").upper(),
                    op.get("urlTemplate") or op.get("url") or "", op.get("description") or "",
                    job["requires_sub"], now,
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


def process_apim(
    apim: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool,
) -> int:
    """Collect and persist one APIM instance (legacy compatible entry point)."""
    collected = collect_apim(apim, subscription_id, dry_run=dry_run)
    return persist_apim(subscription_id, conn, collected, dry_run=dry_run)


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


def harvest_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    """Harvest APIM API→backend routes for every APIM instance in a subscription."""
    collected = collect_routes(subscription_id, dry_run=dry_run)
    return persist_routes(subscription_id, conn, collected, dry_run=dry_run)


def collect_routes(
    subscription_id: str,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Collect all APIM routing bundles without touching SQLite."""
    apim_instances = list_apim_instances(subscription_id)
    if not apim_instances:
        return []

    if len(apim_instances) == 1:
        return [
            collect_apim(apim_instances[0], subscription_id, dry_run=dry_run)
        ]

    bundles: list[dict[str, Any] | None] = [None] * len(apim_instances)
    with ThreadPoolExecutor(max_workers=min(_APIM_FETCH_WORKERS, len(apim_instances))) as pool:
        futures = {
            pool.submit(collect_apim, instance, subscription_id, dry_run=dry_run): index
            for index, instance in enumerate(apim_instances)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                bundles[index] = future.result()
            except Exception as exc:
                instance = apim_instances[index]
                print(f"    [apim-routing] {instance.get('name', '<unknown>')} FAILED ({exc})")
    return [bundle for bundle in bundles if bundle is not None]


def persist_routes(
    subscription_id: str,
    conn: sqlite3.Connection,
    collected: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> int:
    """Persist previously collected APIM routing bundles."""
    _ensure_schema(conn)
    _ensure_apim_schema(conn)
    if not collected:
        print("  No APIM instances found — skipping (0.00s)")
        return 0

    started = time.perf_counter()
    total = 0
    for bundle in collected:
        total += persist_apim(subscription_id, conn, bundle, dry_run=dry_run)
    print(
        f"    [apim-routing] route harvest completed in "
        f"{time.perf_counter() - started:.2f}s ({len(collected)} APIM instance(s))",
        flush=True,
    )
    return total


harvest_routes._post_harvest_split = (  # type: ignore[attr-defined]
    collect_routes,
    persist_routes,
)


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
        total += harvest_routes(sub_id, conn, args.dry_run)

    conn.close()
    print(f"\n[apim-routing] Done. {total} API routes across {len(target_subs)} subscription(s).")


if __name__ == "__main__":
    main()
