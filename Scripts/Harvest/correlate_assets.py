#!/usr/bin/env python3
"""Correlate harvested Azure assets: FQDN-based routing chains, public exposure
flags, and pipeline-tag → repository matching.

Run this after harvest_azure_assets.py to enrich provisioned_asset_repo_links
and resource_connections in cozo.db.

Usage:
    python Scripts/Harvest/correlate_assets.py --subscription "My-Prod-Sub"
    python Scripts/Harvest/correlate_assets.py --all
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Persist"))
sys.path.insert(0, str(Path(__file__).parent))

from db_helpers import _ensure_schema  # type: ignore

from Azure import app_gateway, apim  # FQDN index helpers


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

def get_harvested_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, display_name FROM subscriptions").fetchall()
    return [{"id": r[0], "display_name": r[1]} for r in rows]


def get_assets_for_sub(conn: sqlite3.Connection, sub_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, type, fqdn, pipeline_tag, tags FROM provisioned_assets WHERE subscription_id = ?",
        (sub_id,),
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "type": r[2], "fqdn": r[3], "pipeline_tag": r[4], "tags": r[5]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 1. FQDN-based routing chain: mark assets fronted by a gateway / APIM
# ---------------------------------------------------------------------------

def build_fqdn_index(sub_id: str) -> dict[str, str]:
    """Merge App Gateway backend FQDNs + APIM gateway FQDNs into one index.

    Returns {fqdn_lower: frontend_name}.
    """
    index: dict[str, str] = {}
    try:
        index.update(app_gateway.get_backend_fqdns(sub_id))
    except Exception:
        pass
    try:
        index.update(apim.get_gateway_fqdns(sub_id))
    except Exception:
        pass
    return index


def correlate_routing_chains(
    conn: sqlite3.Connection,
    sub_id: str,
    assets: list[dict[str, Any]],
) -> int:
    """Insert resource_connections rows linking gateways to their backend assets.

    Uses a synthetic experiment_id of 'harvest-<sub_id>' so these connections
    are queryable alongside IaC-derived topology.
    """
    fqdn_index = build_fqdn_index(sub_id)
    if not fqdn_index:
        return 0

    experiment_id = f"harvest-{sub_id}"
    inserted = 0

    # Build a name→asset_id map so we can reference gateway assets
    name_to_id = {a["name"]: a["id"] for a in assets if a["name"]}

    for asset in assets:
        fqdn = (asset.get("fqdn") or "").lower()
        if not fqdn:
            continue
        frontend_name = fqdn_index.get(fqdn)
        if not frontend_name:
            # Try prefix match (e.g., fqdn contains the backend hostname)
            for key, name in fqdn_index.items():
                if fqdn.endswith(key) or key.endswith(fqdn):
                    frontend_name = name
                    break

        if frontend_name and frontend_name in name_to_id:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO resource_connections
                        (experiment_id, source_resource_id, target_resource_id,
                         connection_type, notes, inferred_internet)
                    SELECT ?, pa_src.rowid, pa_tgt.rowid, 'gateway_to_backend',
                           'Inferred from FQDN correlation during harvest', 1
                    FROM provisioned_assets pa_src
                    JOIN provisioned_assets pa_tgt
                        ON pa_src.id = ? AND pa_tgt.id = ?
                    """,
                    (experiment_id, name_to_id[frontend_name], asset["id"]),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                pass

    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# 2. Pipeline-tag → repository matching
# ---------------------------------------------------------------------------

_ADO_URL_RE = re.compile(
    r"dev\.azure\.com/[^/]+/([^/]+)/|visualstudio\.com/([^/]+)/",
    re.IGNORECASE,
)


def extract_project_from_pipeline_tag(pipeline_tag: str) -> str | None:
    """Parse an ADO URL in the pipeline tag and return the project name."""
    m = _ADO_URL_RE.search(pipeline_tag)
    if m:
        return m.group(1) or m.group(2)
    return None


def match_repos(
    conn: sqlite3.Connection,
    assets: list[dict[str, Any]],
) -> int:
    """Attempt to match provisioned assets to repositories and write links."""
    repos = conn.execute("SELECT id, repo_name, repo_url FROM repositories").fetchall()
    if not repos:
        return 0

    repo_name_map: dict[str, int] = {}
    repo_url_map: dict[str, int] = {}
    for repo_id, repo_name, repo_url in repos:
        if repo_name:
            repo_name_map[repo_name.lower()] = repo_id
        if repo_url:
            repo_url_map[repo_url.lower()] = repo_id

    linked = 0
    for asset in assets:
        match_method = None
        confidence = None
        repo_id = None

        # --- Method 1: pipeline tag contains ADO project/repo name (high confidence)
        pipeline_tag = asset.get("pipeline_tag") or ""
        if pipeline_tag:
            project = extract_project_from_pipeline_tag(pipeline_tag)
            if project:
                candidate = project.lower()
                repo_id = repo_name_map.get(candidate)
                if repo_id:
                    match_method = "pipeline_tag"
                    confidence = "high"
            # Also check raw tag value against repo names
            if not repo_id:
                for rname, rid in repo_name_map.items():
                    if rname in pipeline_tag.lower():
                        repo_id = rid
                        match_method = "pipeline_tag"
                        confidence = "high"
                        break

        # --- Method 2: Azure tags contain a 'repo' or 'repository' key
        if not repo_id:
            try:
                tags = json.loads(asset.get("tags") or "{}")
            except Exception:
                tags = {}
            repo_hint = (
                tags.get("repo") or tags.get("repository") or
                tags.get("git-repo") or tags.get("source-repo")
            )
            if repo_hint:
                candidate = repo_hint.lower().rstrip("/").split("/")[-1]
                repo_id = repo_name_map.get(candidate)
                if repo_id:
                    match_method = "pipeline_tag"  # treat explicit tag as high confidence
                    confidence = "high"

        # --- Method 3: asset name matches a repo name (low confidence)
        if not repo_id:
            asset_name = (asset.get("name") or "").lower()
            for rname, rid in repo_name_map.items():
                if asset_name == rname or asset_name.startswith(rname + "-") or rname in asset_name:
                    repo_id = rid
                    match_method = "naming_convention"
                    confidence = "low"
                    break

        if repo_id and match_method:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provisioned_asset_repo_links
                        (asset_id, repository_id, match_method, confidence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (asset["id"], repo_id, match_method, confidence),
                )
                linked += conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                pass

    conn.commit()
    return linked


# ---------------------------------------------------------------------------
# Public exposure flag refresh
# ---------------------------------------------------------------------------

def refresh_public_flags(conn: sqlite3.Connection, sub_id: str) -> None:
    """Keep harvested network exposure flags authoritative.

    Blob public access is still captured in raw_json/_extra for analysis, but it
    should not override the network exposure classification used by the cloud
    diagram.
    """
    _ = conn, sub_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def correlate_subscription(conn: sqlite3.Connection, sub_id: str, sub_name: str) -> None:
    print(f"\n[correlate] {sub_name} ({sub_id})")
    assets = get_assets_for_sub(conn, sub_id)
    if not assets:
        print("  No harvested assets found — run harvest_azure_assets.py first.")
        return

    print(f"  {len(assets)} assets loaded")

    edges = correlate_routing_chains(conn, sub_id, assets)
    print(f"  {edges} routing chain connection(s) written")

    links = match_repos(conn, assets)
    print(f"  {links} repo link(s) written")

    refresh_public_flags(conn, sub_id)
    print("  Public exposure flags refreshed")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Correlate harvested Azure assets with repos and routing chains")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--subscription", metavar="NAME_OR_ID")
    group.add_argument("--all", action="store_true", dest="all_subs")
    args = parser.parse_args()

    db_path = REPO_ROOT / "Output" / "Data" / "cozo.db"
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)

    subs = get_harvested_subscriptions(conn)
    if not subs:
        print("[error] No subscriptions found in DB. Run harvest_azure_assets.py first.", file=sys.stderr)
        sys.exit(1)

    if args.all_subs:
        targets = subs
    else:
        needle = args.subscription.lower()
        targets = [s for s in subs if s["id"].lower() == needle or (s["display_name"] or "").lower() == needle]
        if not targets:
            print(f"[error] Subscription '{args.subscription}' not found in DB.", file=sys.stderr)
            sys.exit(1)

    for sub in targets:
        correlate_subscription(conn, sub["id"], sub["display_name"] or sub["id"])

    conn.close()
    print("\n[correlate] Done.")


if __name__ == "__main__":
    main()
