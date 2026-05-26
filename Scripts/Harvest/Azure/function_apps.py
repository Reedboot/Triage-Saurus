"""Harvest Azure Function Apps."""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, infer_fqdn, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Web/sites"  # function apps share the same ARM type


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["functionapp", "list"], subscription_id)
    results = []

    for app in raw:
        fqdn = safe_str(app.get("defaultHostName")) or infer_fqdn(app)
        tags = app.get("tags") or {}
        pipeline_tag = None
        for key in ("pipeline", "Pipeline", "ado-pipeline", "build-pipeline"):
            if key in tags:
                pipeline_tag = safe_str(tags[key])
                break

        results.append({
            "id": app["id"],
            "subscription_id": subscription_id,
            "resource_group": app.get("resourceGroup"),
            "name": app.get("name"),
            "type": app.get("type", RESOURCE_TYPE),
            "location": app.get("location"),
            "sku": infer_sku(app),
            "tags": json.dumps(tags),
            "is_public": 1,  # function apps are generally public unless network-restricted
            "fqdn": fqdn,
            "pipeline_tag": pipeline_tag,
            "raw_json": json.dumps(app),
        })

    return results
