#!/usr/bin/env python3
"""Build a full APIM → Backend routing map and persist it to cozo.db.

For each APIM instance in the subscription this script:
  1. Lists every API and records its path + backend service URL
  2. Lists every backend entity and records its URL + circuit-breaker config
  3. Cross-references API serviceUrl / named-backend with provisioned_assets.fqdn
     to create resource_connections rows (type='apim_routing')
  4. Stores the raw API→backend mapping in a new apim_api_routes table

Usage:
    python Scripts/Harvest/apim_routing_map.py --subscription "mysub"
    python Scripts/Harvest/apim_routing_map.py --all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
"""


def _ensure_apim_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_APIM_ROUTES_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------

def _az(*args: str, subscription_id: str) -> Any:
    cmd = ["az", *args, "--subscription", subscription_id, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"    [warn] az {' '.join(args[:3])} failed: {result.stderr.strip()[:120]}")
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
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
    apim_name = apim["name"]
    resource_group = apim["resourceGroup"]
    apim_resource_id = apim.get("id")
    now = datetime.now(timezone.utc).isoformat()

    print(f"\n  [apim] {apim_name} (rg={resource_group})")

    # --- Backends ---
    print(f"    fetching backends...", end=" ", flush=True)
    backends_raw = list_backends(apim_name, resource_group, subscription_id)
    # Build lookup: backend name → URL
    backend_map: dict[str, str] = {}
    for b in backends_raw:
        b_name = b.get("name") or ""
        b_url = b.get("url") or ""
        if b_url:
            backend_map[b_name] = b_url
    print(f"{len(backends_raw)} backends")

    # --- APIs ---
    print(f"    fetching APIs...", end=" ", flush=True)
    apis_raw = list_apis(apim_name, resource_group, subscription_id)
    print(f"{len(apis_raw)} APIs")

    # --- Load existing provisioned_assets FQDNs for cross-reference ---
    asset_fqdn_rows = conn.execute(
        "SELECT id, name, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
        (subscription_id,),
    ).fetchall()
    fqdn_to_asset: dict[str, tuple[str, str]] = {
        row[2]: (row[0], row[1]) for row in asset_fqdn_rows if row[2]
    }

    routes_upserted = 0
    connections_created = 0

    apim_asset_rows = conn.execute(
        "SELECT id FROM provisioned_assets WHERE subscription_id = ? AND name = ?",
        (subscription_id, apim_name),
    ).fetchall()
    apim_asset_id = apim_asset_rows[0][0] if apim_asset_rows else apim_resource_id

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
                    api_name, api_display_name, api_path, protocols,
                    backend_id, backend_url, service_url,
                    requires_sub, now,
                ),
            )
            routes_upserted += 1

            # --- Create resource_connection: APIM → backend asset ---
            backend_fqdn = _url_to_fqdn(backend_url)
            if backend_fqdn and not _is_fabric_url(backend_url):
                # Find asset by FQDN (exact or suffix match)
                target_asset_id = None
                for fqdn, (asset_id, asset_name) in fqdn_to_asset.items():
                    if fqdn == backend_fqdn or backend_fqdn.endswith(f".{fqdn}") or fqdn.endswith(f".{backend_fqdn}"):
                        target_asset_id = asset_id
                        break

                conn.execute(
                    """
                    INSERT OR REPLACE INTO resource_connections
                        (source_id, target_id, connection_type, metadata)
                    VALUES (?, ?, 'apim_routing', ?)
                    """,
                    (
                        apim_asset_id,
                        target_asset_id or f"fqdn:{backend_fqdn}",
                        json.dumps({
                            "api_name": api_name,
                            "api_path": api_path,
                            "backend_url": backend_url,
                            "requires_subscription": bool(requires_sub),
                        }),
                    ),
                )
                connections_created += 1

    if not dry_run:
        conn.commit()

    print(f"    → {routes_upserted} routes upserted, {connections_created} connections created")
    return routes_upserted


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
        capture_output=True, text=True, timeout=60,
    )
    all_subs: list[dict] = json.loads(subs_raw.stdout or "[]")

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
