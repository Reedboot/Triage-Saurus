"""Shared helper for simple Azure resource-list harvesters."""
from __future__ import annotations

import json
from typing import Any, Callable

from ._helpers import az, build_endpoints, infer_fqdn, infer_sku

FqdnFn = Callable[[dict[str, Any]], str | None]
BoolFn = Callable[[dict[str, Any]], bool]
ListFn = Callable[[dict[str, Any]], list[str]]
DictFn = Callable[[dict[str, Any]], dict[str, Any]]
StrFn = Callable[[dict[str, Any]], str | None]


def harvest_resource_list(
    subscription_id: str,
    resource_type: str,
    *,
    fqdn_fn: FqdnFn | None = None,
    is_public_fn: BoolFn | None = None,
    is_restricted_fn: BoolFn | None = None,
    auth_methods_fn: ListFn | None = None,
    extra_fn: DictFn | None = None,
    sku_fn: StrFn | None = None,
    pipeline_tag_fn: StrFn | None = None,
) -> list[dict[str, Any]]:
    raw = az(["resource", "list", "--resource-type", resource_type], subscription_id)
    results: list[dict[str, Any]] = []

    for resource in raw:
        fqdn = fqdn_fn(resource) if fqdn_fn else infer_fqdn(resource)
        is_public = 1 if (is_public_fn(resource) if is_public_fn else False) else 0
        is_restricted = 1 if (is_restricted_fn(resource) if is_restricted_fn else False) else 0
        auth_methods = auth_methods_fn(resource) if auth_methods_fn else []
        extra = extra_fn(resource) if extra_fn else {}
        sku = sku_fn(resource) if sku_fn else infer_sku(resource)
        tags = resource.get("tags") or {}
        results.append({
            "id": resource["id"],
            "subscription_id": subscription_id,
            "resource_group": resource.get("resourceGroup"),
            "name": resource.get("name"),
            "type": resource.get("type", resource_type),
            "location": resource.get("location"),
            "sku": sku,
            "tags": json.dumps(tags),
            "is_public": is_public,
            "is_restricted": is_restricted,
            "ip_restrictions": json.dumps([]),
            "endpoints": build_endpoints([(fqdn, 443, "https")] if fqdn else []),
            "auth_methods": json.dumps(auth_methods),
            "fqdn": fqdn,
            "pipeline_tag": pipeline_tag_fn(resource) if pipeline_tag_fn else None,
            "raw_json": json.dumps({**resource, "_extra": extra}),
        })

    return results
