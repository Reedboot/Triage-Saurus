#!/usr/bin/env python3
"""Collect bounded, read-only Azure evidence for an existing resource."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from Azure._helpers import az_resource_show, redact_azure_value  # noqa: E402

MAX_RESOURCES = 50
ALLOWED_FIELDS = {
    "id", "name", "type", "location", "resourceGroup", "sku", "kind",
    "identity", "properties.publicNetworkAccess", "properties.networkAcls",
    "properties.privateEndpointConnections", "properties.minimumTlsVersion",
    "properties.allowBlobPublicAccess", "properties.publicAccess",
}


def _resource_subscription(resource_id: str) -> str:
    parts = resource_id.strip("/").split("/")
    if len(parts) < 2 or parts[0].lower() != "subscriptions":
        raise ValueError("resource ID must be an Azure ARM resource ID")
    return parts[1]


def _select_fields(value: Any, fields: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        current: Any = value
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            result[field] = current
    return result


def collect(
    subscription_id: str,
    resource_ids: list[str],
    fields: list[str],
) -> dict[str, Any]:
    if not resource_ids or len(resource_ids) > MAX_RESOURCES:
        raise ValueError(f"between 1 and {MAX_RESOURCES} resource IDs are required")
    invalid_fields = sorted(set(fields) - ALLOWED_FIELDS)
    if invalid_fields:
        raise ValueError(f"unsupported fields: {', '.join(invalid_fields)}")

    requests = []
    for resource_id in resource_ids:
        if _resource_subscription(resource_id).lower() != subscription_id.lower():
            requests.append({
                "resource_id": resource_id,
                "status": "unresolved",
                "reason": "resource is outside the requested subscription",
            })
            continue
        try:
            resource = az_resource_show(resource_id, subscription_id)
            if resource is None:
                requests.append({
                    "resource_id": resource_id,
                    "status": "unresolved",
                    "reason": "resource detail was unavailable",
                })
                continue
            requests.append({
                "resource_id": resource_id,
                "status": "checked",
                "evidence": redact_azure_value(_select_fields(resource, fields)),
            })
        except (RuntimeError, ValueError) as exc:
            requests.append({
                "resource_id": resource_id,
                "status": "unresolved",
                "reason": str(exc)[:500],
            })
    return {
        "subscription_id": subscription_id,
        "fields": fields,
        "requests": requests,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-id", action="append", required=True)
    parser.add_argument("--field", action="append", dest="fields")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    fields = args.fields or ["id", "name", "type", "location", "properties.publicNetworkAccess"]
    result = collect(args.subscription_id, args.resource_id, fields)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
