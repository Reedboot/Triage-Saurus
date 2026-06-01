#!/usr/bin/env python3
"""Harvest live Azure cloud assets into Triage-Saurus cozo.db.

Usage:
    # Harvest a single subscription (name or GUID)
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub"

    # Harvest all accessible subscriptions
    python Scripts/Harvest/harvest_azure_assets.py --all

    # Dry-run: print what would be harvested without writing to DB
    python Scripts/Harvest/harvest_azure_assets.py --subscription "My-Prod-Sub" --dry-run

Prerequisites:
    - Python 3.9+
    - Azure CLI (az) installed and in PATH — https://aka.ms/installazurecli
    - Logged in:  az login  (or service principal / managed identity)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup so this script works from any cwd
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore

from Azure import app_gateway, apim, web_apps, function_apps, aks, storage, key_vault, sql_server
from Azure import cosmos_db, app_service_plan, service_bus, container_registry, virtual_network
from Azure import redis_cache, event_hub, app_configuration, service_fabric, cognitive_services
from Azure import data_factory, app_service_environment, app_insights, private_endpoint, traffic_manager
from Azure import front_door, firewall
from Azure._helpers import set_probe_enabled
import appgw_routing_map
import apim_routing_map

# ---------------------------------------------------------------------------
# Providers registry — order matters: gateways/APIM first for correlation
# ---------------------------------------------------------------------------
PROVIDERS = [
    # ── Ingress / API layer ──────────────────────────────────────────────
    ("App Gateways",            app_gateway.harvest),
    ("APIM",                    apim.harvest),
    ("Traffic Manager",         traffic_manager.harvest),
    ("Front Door",              front_door.harvest),
    # ── Compute ─────────────────────────────────────────────────────────
    ("App Service Environments",app_service_environment.harvest),
    ("App Service Plans",       app_service_plan.harvest),
    ("Web Apps",                web_apps.harvest),
    ("Function Apps",           function_apps.harvest),
    ("AKS",                     aks.harvest),
    ("Service Fabric",          service_fabric.harvest),
    # ── Data ────────────────────────────────────────────────────────────
    ("Cosmos DB",               cosmos_db.harvest),
    ("SQL Servers",             sql_server.harvest),
    ("Redis Cache",             redis_cache.harvest),
    ("Storage",                 storage.harvest),
    # ── Messaging / Integration ──────────────────────────────────────────
    ("Event Hubs",              event_hub.harvest),
    ("Service Bus",             service_bus.harvest),
    ("Data Factory",            data_factory.harvest),
    # ── AI / ML ─────────────────────────────────────────────────────────
    ("Cognitive Services",      cognitive_services.harvest),
    # ── Security / Config ────────────────────────────────────────────────
    ("Key Vaults",              key_vault.harvest),
    ("App Configuration",       app_configuration.harvest),
    ("Container Registries",    container_registry.harvest),
    ("Private Endpoints",       private_endpoint.harvest),
    # ── Observability ────────────────────────────────────────────────────
    ("App Insights",            app_insights.harvest),
    # ── Networking ───────────────────────────────────────────────────────
    ("Virtual Networks",        virtual_network.harvest),
    ("Firewalls",               firewall.harvest),
]


# ---------------------------------------------------------------------------
# Prerequisites check
# ---------------------------------------------------------------------------

def check_prerequisites() -> bool:
    """Verify az CLI is installed and the user is logged in. Returns True if OK."""
    ok = True

    # 1. Python version
    if sys.version_info < (3, 9):
        print(f"[prereq] ✗ Python 3.9+ required (running {sys.version.split()[0]})", file=sys.stderr)
        ok = False
    else:
        print(f"[prereq] ✓ Python {sys.version.split()[0]}")

    # 2. Azure CLI installed
    az_path = shutil.which("az")
    if not az_path:
        print("[prereq] ✗ Azure CLI not found in PATH.", file=sys.stderr)
        print("         Install from: https://aka.ms/installazurecli", file=sys.stderr)
        ok = False
    else:
        try:
            ver = subprocess.run(
                ["az", "version", "--output", "json"],
                capture_output=True, text=True, timeout=15,
            )
            az_ver = json.loads(ver.stdout or "{}").get("azure-cli", "unknown")
            print(f"[prereq] ✓ Azure CLI {az_ver} ({az_path})")
        except Exception:
            print(f"[prereq] ✓ Azure CLI found ({az_path})")

    if not ok:
        return False

    # 3. Logged in to Azure
    login_check = subprocess.run(
        ["az", "account", "show", "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if login_check.returncode != 0:
        print("[prereq] ✗ Not logged in to Azure.", file=sys.stderr)
        print("         Run: az login", file=sys.stderr)
        print("         Or for service principal: az login --service-principal -u <appId> -p <password> --tenant <tenant>", file=sys.stderr)
        return False

    try:
        account = json.loads(login_check.stdout)
        print(f"[prereq] ✓ Logged in as: {account.get('user', {}).get('name', 'unknown')} "
              f"(tenant: {account.get('tenantId', '?')})")
    except Exception:
        print("[prereq] ✓ Logged in to Azure")

    return True


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

def list_subscriptions() -> list[dict[str, Any]]:
    """Return all accessible subscriptions via az account list."""
    result = subprocess.run(
        ["az", "account", "list", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"[error] az account list failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    return json.loads(result.stdout or "[]") or []


def resolve_subscription(name_or_id: str, all_subs: list[dict]) -> dict | None:
    """Find a subscription by display name or GUID (case-insensitive)."""
    needle = name_or_id.lower()
    for sub in all_subs:
        if sub.get("id", "").lower() == needle:
            return sub
        if sub.get("name", "").lower() == needle:
            return sub
    return None


def infer_environment(display_name: str) -> str:
    name = display_name.lower()
    if any(k in name for k in ("prod", "production", "live")):
        return "prod"
    if any(k in name for k in ("staging", "stage", "sim", "uat", "preprod")):
        return "staging"
    if any(k in name for k in ("dev", "develop", "sandbox", "test")):
        return "dev"
    if any(k in name for k in ("shared", "platform", "infra", "core", "hub")):
        return "shared"
    return "unknown"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def upsert_subscription(conn: sqlite3.Connection, sub: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO subscriptions (id, display_name, tenant_id, environment, state, last_synced)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name = excluded.display_name,
            tenant_id    = excluded.tenant_id,
            environment  = excluded.environment,
            state        = excluded.state,
            last_synced  = excluded.last_synced
        """,
        (
            sub["id"],
            sub.get("name") or sub.get("displayName"),
            sub.get("tenantId"),
            infer_environment(sub.get("name") or sub.get("displayName") or ""),
            sub.get("state", "Enabled"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def upsert_asset(conn: sqlite3.Connection, asset: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO provisioned_assets
            (id, subscription_id, resource_group, name, type, location, sku,
             tags, is_public, fqdn, pipeline_tag, raw_json, first_detected, last_synced, status,
             is_restricted, ip_restrictions, endpoints, auth_methods, waf_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            subscription_id = excluded.subscription_id,
            resource_group  = excluded.resource_group,
            name            = excluded.name,
            type            = excluded.type,
            location        = excluded.location,
            sku             = excluded.sku,
            tags            = excluded.tags,
            is_public       = excluded.is_public,
            fqdn            = excluded.fqdn,
            pipeline_tag    = excluded.pipeline_tag,
            raw_json        = excluded.raw_json,
            last_synced     = excluded.last_synced,
            status          = 'active',
            first_detected  = COALESCE(provisioned_assets.first_detected, excluded.first_detected),
            is_restricted   = excluded.is_restricted,
            ip_restrictions = excluded.ip_restrictions,
            endpoints       = excluded.endpoints,
            auth_methods    = excluded.auth_methods,
            waf_mode        = excluded.waf_mode
        """,
        (
            asset["id"],
            asset.get("subscription_id"),
            asset.get("resource_group"),
            asset.get("name"),
            asset.get("type"),
            asset.get("location"),
            asset.get("sku"),
            asset.get("tags"),
            asset.get("is_public", 0),
            asset.get("fqdn"),
            asset.get("pipeline_tag"),
            asset.get("raw_json"),
            now,  # first_detected — preserved by COALESCE on conflict
            now,  # last_synced
            asset.get("is_restricted", 0),
            asset.get("ip_restrictions"),
            asset.get("endpoints"),
            asset.get("auth_methods"),
            asset.get("waf_mode"),
        ),
    )


# ---------------------------------------------------------------------------
# Stale asset detection
# ---------------------------------------------------------------------------

def mark_stale_assets(
    conn: sqlite3.Connection,
    sub_id: str,
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    """Mark assets not seen in this harvest run as potentially_removed.

    Returns the list of newly-stale assets for reporting.
    """
    # Find active assets for this subscription that were NOT in this harvest
    rows = conn.execute(
        """
        SELECT id, name, type, resource_group, first_detected, last_synced
        FROM provisioned_assets
        WHERE subscription_id = ? AND status = 'active'
        """,
        (sub_id,),
    ).fetchall()

    stale = []
    for row in rows:
        if row[0] not in seen_ids:
            conn.execute(
                "UPDATE provisioned_assets SET status = 'potentially_removed' WHERE id = ?",
                (row[0],),
            )
            stale.append({
                "id": row[0], "name": row[1], "type": row[2],
                "resource_group": row[3], "first_detected": row[4], "last_synced": row[5],
            })

    return stale


# ---------------------------------------------------------------------------
# Harvest one subscription
# ---------------------------------------------------------------------------

def harvest_subscription(
    sub: dict[str, Any],
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> int:
    sub_id = sub["id"]
    sub_name = sub.get("name") or sub.get("displayName") or sub_id
    print(f"\n[subscription] {sub_name} ({sub_id})")

    if not dry_run:
        upsert_subscription(conn, sub)

    seen_ids: set[str] = set()
    total = 0
    for label, provider_fn in PROVIDERS:
        print(f"  [{label}] harvesting...", end=" ", flush=True)
        try:
            assets = provider_fn(sub_id)
        except Exception as exc:
            print(f"FAILED ({exc})")
            continue

        if not assets:
            print("0 assets")
            continue

        if dry_run:
            print(f"{len(assets)} assets (dry-run, not written)")
        else:
            for asset in assets:
                upsert_asset(conn, asset)
                seen_ids.add(asset["id"])
            conn.commit()
            print(f"{len(assets)} assets")

        total += len(assets)

    # Stale detection (skip on dry-run)
    if not dry_run and seen_ids:
        stale = mark_stale_assets(conn, sub_id, seen_ids)
        conn.commit()
        if stale:
            print(f"\n  ⚠ {len(stale)} asset(s) not seen this run — marked as 'potentially_removed':")
            for a in stale:
                print(f"    - {a['type']}/{a['name']} (rg: {a['resource_group']}, last seen: {a['last_synced']})")
            print("  To confirm removal:  UPDATE provisioned_assets SET status='removed' WHERE id='<id>';")
            print("  To restore (false positive):  UPDATE provisioned_assets SET status='active' WHERE id='<id>';")

    # App Gateway routing + rewrites + WAF — runs after assets so fqdn_to_asset lookup is populated
    print(f"  [App Gateway Routing] harvesting listeners, routing rules, rewrite rules & WAF policies...", flush=True)
    try:
        rules, rewrite_sets, rewrite_rules, waf = appgw_routing_map.harvest_routing(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(
            f"  [App Gateway Routing] {rules} routing rules, "
            f"{rewrite_sets} rewrite rule sets ({rewrite_rules} rewrite rules), "
            f"{waf} WAF policies {action}"
        )
    except Exception as exc:
        print(f"  [App Gateway Routing] FAILED ({exc})")

    # AKS ingress → service → deployment route model
    print(f"  [AKS Routes] harvesting ingress→service→deployment mappings...", flush=True)
    try:
        route_count = aks.harvest_routes(sub_id, conn, dry_run=dry_run)
        action = "would harvest" if dry_run else "written"
        print(f"  [AKS Routes] {route_count} routes {action}")
    except Exception as exc:
        print(f"  [AKS Routes] FAILED ({exc})")

    # APIM API → backend routes
    print(f"  [APIM Routes] harvesting API→backend mappings...", flush=True)
    try:
        route_count = apim.harvest_routes(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [APIM Routes] {route_count} routes {action}")
    except Exception as exc:
        print(f"  [APIM Routes] FAILED ({exc})")

    # APIM backend inventory + API-to-backend links
    print(f"  [APIM Backend Links] harvesting backend inventory and route links...", flush=True)
    try:
        backend_count, link_count = apim_routing_map.harvest_backends(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [APIM Backend Links] {backend_count} backends, {link_count} links {action}")
    except Exception as exc:
        print(f"  [APIM Backend Links] FAILED ({exc})")

    # Function App HTTP triggers
    print(f"  [Function App Triggers] harvesting HTTP trigger routes...", flush=True)
    try:
        trigger_count = function_apps.harvest_http_triggers(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Function App Triggers] {trigger_count} triggers {action}")
    except Exception as exc:
        print(f"  [Function App Triggers] FAILED ({exc})")

    # Front Door routing rules
    print(f"  [Front Door Routes] harvesting routing rules...", flush=True)
    try:
        fd_count = front_door.harvest_routes(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Front Door Routes] {fd_count} routes {action}")
    except Exception as exc:
        print(f"  [Front Door Routes] FAILED ({exc})")

    # Azure Firewall NAT + app rules
    print(f"  [Firewall Rules] harvesting NAT and application rules...", flush=True)
    try:
        nat_count, app_count = firewall.harvest_rules(sub_id, conn, dry_run=dry_run)
        action = "would write" if dry_run else "written"
        print(f"  [Firewall Rules] {nat_count} NAT rules, {app_count} app rules {action}")
    except Exception as exc:
        print(f"  [Firewall Rules] FAILED ({exc})")

    print(f"  [total] {total} assets for {sub_name}")
    return total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest live Azure cloud assets into Triage-Saurus cozo.db"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID", help="Subscription name or GUID to harvest")
    group.add_argument("--all", action="store_true", dest="all_subs", help="Harvest all accessible subscriptions")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be harvested without writing to DB")
    parser.add_argument("--skip-prereq-check", action="store_true", help="Skip prerequisites check")
    parser.add_argument("--skip-probes", action="store_true",
                        help="Skip active connectivity probes (faster, no network connections made)")
    args = parser.parse_args()

    set_probe_enabled(not args.skip_probes)

    if not args.skip_prereq_check:
        print("[prereq] Checking prerequisites...")
        if not check_prerequisites():
            sys.exit(1)
        print()

    print("[harvest] Listing accessible Azure subscriptions...")
    all_subs = list_subscriptions()
    if not all_subs:
        print("[error] No subscriptions found. Make sure you are logged in: az login", file=sys.stderr)
        sys.exit(1)

    if args.all_subs:
        target_subs = all_subs
    else:
        sub = resolve_subscription(args.subscription, all_subs)
        if not sub:
            names = [s.get("name") for s in all_subs]
            print(f"[error] Subscription '{args.subscription}' not found. Available: {names}", file=sys.stderr)
            sys.exit(1)
        target_subs = [sub]

    db_path = REPO_ROOT / "Output" / "Data" / "cozo.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)

    grand_total = 0
    for sub in target_subs:
        grand_total += harvest_subscription(sub, conn, dry_run=args.dry_run)

    conn.close()
    print(f"\n[harvest] Done. {grand_total} assets across {len(target_subs)} subscription(s).")
    if not args.dry_run:
        print(f"[harvest] Stored in: {db_path}")


if __name__ == "__main__":
    main()
