#!/usr/bin/env python3
"""Harvest Azure Private DNS zones, records, and VNet links into cozo.db.

For each private DNS zone in the subscription this script:
  1. Fetches all record sets (A, AAAA, CNAME, TXT, MX, SRV, PTR) via
     `az network private-dns record-set list` (one call per zone)
  2. Fetches VNet links per zone to understand resolution scope
  3. Classifies zones: privatelink / internal-service / apim / ase / custom
  4. Cross-references A-record names with provisioned_assets and apim_api_routes
     to create resource_connections rows (type='dns_resolution')
  5. Flags privatelink.* zones with zero A records (PE created but DNS not registered)
  6. Identifies ExternalDNS-managed records (TXT ownership markers = a-<hostname>)

Tables populated:
  - private_dns_zones       (one row per zone)
  - private_dns_records     (one row per record set)
  - private_dns_vnet_links  (one row per VNet link)
  - resource_connections    (dns_resolution type edges)

Usage:
    python Scripts/Harvest/private_dns_map.py --subscription "pipeline-customer-production"
    python Scripts/Harvest/private_dns_map.py --all
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

from db_helpers import _ensure_schema  # type: ignore

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DNS_DDL = """
CREATE TABLE IF NOT EXISTS private_dns_zones (
    id                  TEXT PRIMARY KEY,   -- resource id
    subscription_id     TEXT NOT NULL,
    name                TEXT NOT NULL,      -- e.g. internal.cbinnovation.uk
    resource_group      TEXT,
    zone_type           TEXT,               -- privatelink | internal | apim | ase | custom
    record_count        INTEGER DEFAULT 0,
    vnet_link_count     INTEGER DEFAULT 0,
    a_record_count      INTEGER DEFAULT 0,
    cname_count         INTEGER DEFAULT 0,
    externaldns_managed INTEGER DEFAULT 0,  -- 1 if TXT ownership markers found
    last_synced         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_pdns_zones_sub  ON private_dns_zones(subscription_id);
CREATE INDEX IF NOT EXISTS idx_pdns_zones_name ON private_dns_zones(name);

CREATE TABLE IF NOT EXISTS private_dns_records (
    id              TEXT PRIMARY KEY,   -- {zone_name}::{record_type}::{record_name}
    subscription_id TEXT NOT NULL,
    zone_name       TEXT NOT NULL,
    record_name     TEXT NOT NULL,      -- relative name (@ = apex)
    fqdn            TEXT,               -- fully-qualified: record_name.zone_name
    record_type     TEXT NOT NULL,      -- A | CNAME | TXT | MX | SRV | PTR
    ip_addresses    TEXT,               -- JSON array (A records)
    cname_target    TEXT,               -- CNAME target
    txt_values      TEXT,               -- JSON array (TXT records)
    ttl             INTEGER,
    managed_by      TEXT,               -- externaldns | azure | manual
    last_synced     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_pdns_records_sub  ON private_dns_records(subscription_id);
CREATE INDEX IF NOT EXISTS idx_pdns_records_zone ON private_dns_records(zone_name);
CREATE INDEX IF NOT EXISTS idx_pdns_records_fqdn ON private_dns_records(fqdn);

CREATE TABLE IF NOT EXISTS private_dns_vnet_links (
    id                   TEXT PRIMARY KEY,  -- resource id
    subscription_id      TEXT NOT NULL,
    zone_name            TEXT NOT NULL,
    link_name            TEXT NOT NULL,
    vnet_id              TEXT,
    vnet_name            TEXT,
    registration_enabled INTEGER DEFAULT 0, -- auto-register VM DNS names
    link_state           TEXT,              -- Completed | InProgress
    last_synced          DATETIME
);
CREATE INDEX IF NOT EXISTS idx_pdns_links_sub  ON private_dns_vnet_links(subscription_id);
CREATE INDEX IF NOT EXISTS idx_pdns_links_zone ON private_dns_vnet_links(zone_name);
"""


def _ensure_dns_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DNS_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Azure CLI helpers
# ---------------------------------------------------------------------------

def _az(*args: str, subscription_id: str, timeout: int = 120) -> Any:
    cmd = ["az", *args, "--subscription", subscription_id, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None


def list_zones(subscription_id: str) -> list[dict]:
    return _az("network", "private-dns", "zone", "list", subscription_id=subscription_id) or []


def list_records(zone_name: str, rg: str, subscription_id: str) -> list[dict]:
    return _az(
        "network", "private-dns", "record-set", "list",
        "--zone-name", zone_name, "-g", rg,
        subscription_id=subscription_id,
    ) or []


def list_vnet_links(zone_name: str, rg: str, subscription_id: str) -> list[dict]:
    return _az(
        "network", "private-dns", "link", "vnet", "list",
        "--zone-name", zone_name, "-g", rg,
        subscription_id=subscription_id,
    ) or []


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------

def _classify_zone(name: str) -> str:
    if name.startswith("privatelink."):
        return "privatelink"
    if "azure-api.net" in name:
        return "apim"
    if "appserviceenvironment.net" in name:
        return "ase"
    if any(k in name for k in ("internal.", "private.")):
        return "internal"
    return "custom"


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------

def _extract_records(record_set: dict, zone_name: str) -> dict | None:
    rtype = record_set.get("type", "").split("/")[-1].upper()
    if rtype == "SOA":
        return None  # skip SOA

    rname = record_set.get("name", "")
    props = record_set.get("properties") or {}
    ttl   = props.get("ttl")

    fqdn = f"{rname}.{zone_name}" if rname != "@" else zone_name

    a_records   = props.get("aRecords") or []
    aaaa_records = props.get("aaaaRecords") or []
    cname_rec   = props.get("cnameRecord") or {}
    txt_recs    = props.get("txtRecords") or []
    mx_recs     = props.get("mxRecords") or []

    ip_addresses: list[str] = (
        [r.get("ipv4Address") for r in a_records if r.get("ipv4Address")] +
        [r.get("ipv6Address") for r in aaaa_records if r.get("ipv6Address")]
    )
    cname_target = cname_rec.get("cname")
    txt_values   = [" ".join(r.get("value", [])) for r in txt_recs]

    # Detect ExternalDNS ownership records: TXT named "a-<service>"
    managed_by = "manual"
    if rtype == "TXT" and rname.startswith("a-"):
        managed_by = "externaldns"
    elif rtype == "A" and not ip_addresses:
        # A record with no IPs yet - ExternalDNS placeholder
        managed_by = "externaldns"
    elif rtype == "A" and ip_addresses:
        managed_by = "azure" if fqdn.endswith(".privatelink." + zone_name.split(".", 1)[-1]) else "externaldns"

    return {
        "id": f"{zone_name}::{rtype}::{rname}",
        "record_name": rname,
        "fqdn": fqdn,
        "record_type": rtype,
        "ip_addresses": json.dumps(ip_addresses) if ip_addresses else None,
        "cname_target": cname_target,
        "txt_values": json.dumps(txt_values) if txt_values else None,
        "ttl": ttl,
        "managed_by": managed_by,
    }


# ---------------------------------------------------------------------------
# Process one zone
# ---------------------------------------------------------------------------

def process_zone(
    zone: dict,
    subscription_id: str,
    conn: sqlite3.Connection,
    fqdn_to_asset: dict[str, tuple[str, str]],
    apim_backend_fqdns: set[str],
    dry_run: bool,
    now: str,
) -> dict:
    zone_name = zone["name"]
    rg        = zone["resourceGroup"]
    zone_id   = zone.get("id", f"{subscription_id}/{zone_name}")
    zone_type = _classify_zone(zone_name)

    # --- Records ---
    records_raw = list_records(zone_name, rg, subscription_id)
    records = [r for raw in records_raw if (r := _extract_records(raw, zone_name))]

    a_records     = [r for r in records if r["record_type"] == "A"]
    cname_records = [r for r in records if r["record_type"] == "CNAME"]
    externaldns   = any(r["managed_by"] == "externaldns" for r in records)

    # --- VNet links ---
    links_raw = list_vnet_links(zone_name, rg, subscription_id)

    stats = {
        "record_count": len(records),
        "a_count": len(a_records),
        "cname_count": len(cname_records),
        "link_count": len(links_raw),
        "externaldns": externaldns,
    }

    if dry_run:
        return stats

    # Upsert zone
    conn.execute(
        """
        INSERT INTO private_dns_zones
            (id, subscription_id, name, resource_group, zone_type,
             record_count, vnet_link_count, a_record_count, cname_count,
             externaldns_managed, last_synced)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            record_count        = excluded.record_count,
            vnet_link_count     = excluded.vnet_link_count,
            a_record_count      = excluded.a_record_count,
            cname_count         = excluded.cname_count,
            externaldns_managed = excluded.externaldns_managed,
            last_synced         = excluded.last_synced
        """,
        (zone_id, subscription_id, zone_name, rg, zone_type,
         len(records), len(links_raw), len(a_records), len(cname_records),
         1 if externaldns else 0, now),
    )

    # Upsert records + create resource_connections
    for rec in records:
        conn.execute(
            """
            INSERT INTO private_dns_records
                (id, subscription_id, zone_name, record_name, fqdn,
                 record_type, ip_addresses, cname_target, txt_values,
                 ttl, managed_by, last_synced)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                ip_addresses = excluded.ip_addresses,
                cname_target = excluded.cname_target,
                txt_values   = excluded.txt_values,
                ttl          = excluded.ttl,
                managed_by   = excluded.managed_by,
                last_synced  = excluded.last_synced
            """,
            (
                rec["id"], subscription_id, zone_name, rec["record_name"], rec["fqdn"],
                rec["record_type"], rec["ip_addresses"], rec["cname_target"],
                rec["txt_values"], rec["ttl"], rec["managed_by"], now,
            ),
        )

        # resource_connections: DNS A/CNAME → target asset
        target_fqdn = rec["fqdn"]
        source_asset_id = None
        target_asset_id = None

        # Find source asset (what resolves to this name?)
        for known_fqdn, (asset_id, _) in fqdn_to_asset.items():
            if known_fqdn == target_fqdn or known_fqdn.endswith(f".{target_fqdn}"):
                source_asset_id = asset_id
                break

        # Find target asset (for CNAME → target, or A-record IP match)
        if rec["cname_target"]:
            cname_host = rec["cname_target"].rstrip(".")
            for known_fqdn, (asset_id, _) in fqdn_to_asset.items():
                if known_fqdn == cname_host or cname_host.endswith(f".{known_fqdn}"):
                    target_asset_id = asset_id
                    break

        if source_asset_id or target_asset_id or rec["record_type"] in ("A", "CNAME"):
            # Check if this hostname is an APIM backend
            is_apim_backend = rec["record_name"] in apim_backend_fqdns or rec["fqdn"] in apim_backend_fqdns

            conn.execute(
                """
                INSERT OR REPLACE INTO resource_connections
                    (source_id, target_id, connection_type, metadata)
                VALUES (?, ?, 'dns_resolution', ?)
                """,
                (
                    source_asset_id or f"fqdn:{target_fqdn}",
                    target_asset_id or (f"fqdn:{rec['cname_target']}" if rec["cname_target"] else f"zone:{zone_name}"),
                    json.dumps({
                        "zone": zone_name,
                        "record_type": rec["record_type"],
                        "record_name": rec["record_name"],
                        "fqdn": rec["fqdn"],
                        "ips": json.loads(rec["ip_addresses"]) if rec["ip_addresses"] else [],
                        "cname": rec["cname_target"],
                        "managed_by": rec["managed_by"],
                        "is_apim_backend": is_apim_backend,
                    }),
                ),
            )

    # Upsert VNet links
    for link in links_raw:
        lp = link.get("properties") or {}
        vnet_id   = (lp.get("virtualNetwork") or {}).get("id", "")
        vnet_name = vnet_id.split("/")[-1] if vnet_id else None
        conn.execute(
            """
            INSERT INTO private_dns_vnet_links
                (id, subscription_id, zone_name, link_name, vnet_id, vnet_name,
                 registration_enabled, link_state, last_synced)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                link_state           = excluded.link_state,
                registration_enabled = excluded.registration_enabled,
                last_synced          = excluded.last_synced
            """,
            (
                link.get("id", f"{zone_name}/{link['name']}"),
                subscription_id, zone_name, link["name"],
                vnet_id, vnet_name,
                1 if lp.get("registrationEnabled") else 0,
                lp.get("virtualNetworkLinkState"),
                now,
            ),
        )

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Post-harvest checks
# ---------------------------------------------------------------------------

def _check_issues(conn: sqlite3.Connection, subscription_id: str) -> None:
    print("\n  [checks]")

    # 1. Privatelink zones with zero A records (PE exists, DNS not registered)
    rows = conn.execute(
        """
        SELECT name FROM private_dns_zones
        WHERE subscription_id = ? AND zone_type = 'privatelink' AND a_record_count = 0
        """,
        (subscription_id,),
    ).fetchall()
    if rows:
        print(f"  ⚠  {len(rows)} privatelink zone(s) have no A records — private endpoint DNS may not be registered:")
        for (name,) in rows[:10]:
            print(f"    - {name}")

    # 2. Internal zones with ExternalDNS but zero A record IPs (AKS not started / ingress IP pending)
    rows2 = conn.execute(
        """
        SELECT zone_name, COUNT(*) as cnt
        FROM private_dns_records
        WHERE subscription_id = ? AND record_type = 'A'
          AND (ip_addresses IS NULL OR ip_addresses = '[]')
          AND managed_by = 'externaldns'
        GROUP BY zone_name
        """,
        (subscription_id,),
    ).fetchall()
    if rows2:
        print(f"  ⚠  {sum(r[1] for r in rows2)} ExternalDNS A record(s) have no IP assigned (AKS ingress pending?):")
        for zone_name, cnt in rows2[:5]:
            print(f"    - {zone_name}: {cnt} records")

    # 3. Zones with no VNet links (DNS zone exists but nothing can resolve it)
    rows3 = conn.execute(
        """
        SELECT name, zone_type, record_count FROM private_dns_zones
        WHERE subscription_id = ? AND vnet_link_count = 0 AND record_count > 1
        ORDER BY record_count DESC
        """,
        (subscription_id,),
    ).fetchall()
    if rows3:
        print(f"  ℹ  {len(rows3)} zone(s) have records but no VNet links (may rely on cross-subscription links):")
        for (name, ztype, cnt) in rows3[:8]:
            print(f"    - {name} ({ztype}, {cnt} records)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest Azure Private DNS zones and records into cozo.db"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID")
    group.add_argument("--all", action="store_true", dest="all_subs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

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
    _ensure_dns_schema(conn)

    now = datetime.now(timezone.utc).isoformat()

    for sub in target_subs:
        sub_id   = sub["id"]
        sub_name = sub.get("name") or sub_id
        print(f"\n[subscription] {sub_name}")

        zones = list_zones(sub_id)
        if not zones:
            print("  No private DNS zones found")
            continue

        # Build FQDN → asset lookup
        fqdn_to_asset: dict[str, tuple[str, str]] = {
            row[2]: (row[0], row[1])
            for row in conn.execute(
                "SELECT id, name, fqdn FROM provisioned_assets WHERE subscription_id = ? AND fqdn IS NOT NULL",
                (sub_id,),
            ).fetchall()
        }

        # Build set of APIM backend hostnames for cross-referencing
        apim_backend_fqdns: set[str] = set()
        for (url,) in conn.execute(
            "SELECT backend_url FROM apim_api_routes WHERE subscription_id = ? AND backend_url IS NOT NULL",
            (sub_id,),
        ).fetchall():
            host = url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
            if host:
                apim_backend_fqdns.add(host)

        print(f"  {len(zones)} zones to process...")
        total_records = 0
        zone_types: dict[str, int] = {}

        for zone in zones:
            zone_name = zone["name"]
            zone_type = _classify_zone(zone_name)
            zone_types[zone_type] = zone_types.get(zone_type, 0) + 1

            stats = process_zone(
                zone, sub_id, conn, fqdn_to_asset, apim_backend_fqdns,
                args.dry_run, now,
            )
            total_records += stats["record_count"]
            if stats["record_count"] > 1:  # skip empty zones
                print(
                    f"  {'[dry]' if args.dry_run else '     '} "
                    f"{zone_name:70s} "
                    f"type={zone_type:12s} "
                    f"records={stats['record_count']:4d}  "
                    f"A={stats['a_count']:3d}  "
                    f"links={stats['link_count']}"
                    + ("  [ExternalDNS]" if stats["externaldns"] else "")
                )

        print(f"\n  Summary: {len(zones)} zones, {total_records} records")
        print(f"  Zone types: {zone_types}")

        if not args.dry_run:
            _check_issues(conn, sub_id)

    conn.close()
    print(f"\n[private-dns] Done.")


if __name__ == "__main__":
    main()
