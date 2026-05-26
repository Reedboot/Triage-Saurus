"""Harvest Azure Cosmos DB accounts."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.DocumentDB/databaseAccounts"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["cosmosdb", "list"], subscription_id)
    results = []

    for acct in raw:
        props = acct.get("properties") or {}
        fqdn = safe_str(props.get("documentEndpoint", "").replace("https://", "").rstrip("/")) or None

        extra = {
            "kind": acct.get("kind"),
            "api": _infer_api(acct),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "enable_free_tier": props.get("enableFreeTier", False),
            "enable_multiple_write_locations": props.get("enableMultipleWriteLocations", False),
            "backup_policy": (props.get("backupPolicy") or {}).get("type"),
            "locations": [loc.get("locationName") for loc in (props.get("readLocations") or [])],
            "ip_rules_count": len(props.get("ipRules") or []),
        }

        # Public if network access is Enabled AND no IP rules and no VNet rules
        ip_rules = props.get("ipRules") or []
        vnet_rules = props.get("virtualNetworkRules") or []
        is_public = (
            1 if props.get("publicNetworkAccess", "Enabled") == "Enabled"
            and not ip_rules and not vnet_rules
            else 0
        )

        results.append({
            "id": acct["id"],
            "subscription_id": subscription_id,
            "resource_group": acct.get("resourceGroup"),
            "name": acct.get("name"),
            "type": acct.get("type", RESOURCE_TYPE),
            "location": acct.get("location"),
            "sku": None,
            "tags": json.dumps(acct.get("tags") or {}),
            "is_public": is_public,
            "fqdn": fqdn,
            "pipeline_tag": (acct.get("tags") or {}).get("pipeline") or (acct.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**acct, "_extra": extra}),
        })

    return results


def _infer_api(acct: dict[str, Any]) -> str:
    """Return a human-readable Cosmos DB API name."""
    caps = {c.get("name") for c in (acct.get("properties", {}).get("capabilities") or [])}
    kind = acct.get("kind", "")
    if "EnableMongo" in caps or kind == "MongoDB":
        return "MongoDB"
    if "EnableCassandra" in caps:
        return "Cassandra"
    if "EnableTable" in caps:
        return "Table"
    if "EnableGremlin" in caps:
        return "Gremlin"
    return "NoSQL"
