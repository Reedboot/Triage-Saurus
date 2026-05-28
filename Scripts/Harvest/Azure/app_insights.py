"""Harvest Azure Application Insights components.

With 179 components in this subscription, App Insights are important context:
they tell us which services are instrumented and link to Log Analytics workspaces.
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az

RESOURCE_TYPE = "Microsoft.Insights/components"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(
        ["resource", "list", "--resource-type", RESOURCE_TYPE],
        subscription_id,
    )
    results = []

    for comp in raw:
        props = comp.get("properties") or {}

        extra = {
            "application_type": props.get("Application_Type"),
            "flow_type": props.get("Flow_Type"),
            "workspace_resource_id": props.get("WorkspaceResourceId"),
            "instrumentation_key": props.get("InstrumentationKey"),  # redacted in raw_json below
            "connection_string_redacted": True,
            "sampling_percentage": props.get("SamplingPercentage"),
            "retention_in_days": props.get("RetentionInDays"),
            "public_network_access_for_ingestion": props.get("publicNetworkAccessForIngestion", "Enabled"),
            "public_network_access_for_query": props.get("publicNetworkAccessForQuery", "Enabled"),
        }

        # Redact instrumentation key from stored JSON
        safe_props = {k: v for k, v in props.items() if k not in ("InstrumentationKey", "ConnectionString")}

        results.append({
            "id": comp["id"],
            "subscription_id": subscription_id,
            "resource_group": comp.get("resourceGroup"),
            "name": comp.get("name"),
            "type": comp.get("type", RESOURCE_TYPE),
            "location": comp.get("location"),
            "sku": None,
            "tags": json.dumps(comp.get("tags") or {}),
            "is_public": 0,  # App Insights is an observability sink, not a public endpoint
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps(["azure_ad", "instrumentation_key"]),
            "fqdn": None,
            "pipeline_tag": (comp.get("tags") or {}).get("pipeline") or (comp.get("tags") or {}).get("ado-pipeline"),
            "raw_json": json.dumps({**comp, "properties": safe_props, "_extra": extra}),
        })

    return results
