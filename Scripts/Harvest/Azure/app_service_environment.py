"""Harvest Azure App Service Environments (ASE v2/v3).

All Web Apps and Function Apps running inside an ASE have hostnames like
  <app>.{ase-name}.appserviceenvironment.net
This provider captures the ASE itself so the web UI can show containment.
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import az, safe_str

RESOURCE_TYPE = "Microsoft.Web/hostingEnvironments"


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    raw = az(["appservice", "ase", "list"], subscription_id)
    results = []

    for ase in raw:
        props = ase.get("properties") or {}

        # Internal ASE DNS suffix: <ase-name>.<region>.appserviceenvironment.net
        dns_suffix = safe_str(props.get("dnsSuffix"))

        extra = {
            "kind": ase.get("kind"),          # ASEV2 or ASEV3
            "status": props.get("status"),
            "internal_load_balancing_mode": props.get("internalLoadBalancingMode", "None"),
            "is_ilb": props.get("internalLoadBalancingMode", "None") != "None",
            "dns_suffix": dns_suffix,
            "maximum_number_of_machines": props.get("maximumNumberOfMachines"),
            "front_end_scale_factor": props.get("frontEndScaleFactor"),
            "worker_pools": len(props.get("workerPools") or []),
            "virtual_network": (props.get("virtualNetwork") or {}).get("id"),
            "subnet": (props.get("virtualNetwork") or {}).get("subnet"),
            "upgrade_availability": props.get("upgradeAvailability"),
        }

        # ILB ASE = no public internet ingress
        is_public = 0 if extra["is_ilb"] else 1

        results.append({
            "id": ase["id"],
            "subscription_id": subscription_id,
            "resource_group": ase.get("resourceGroup"),
            "name": ase.get("name"),
            "type": ase.get("type", RESOURCE_TYPE),
            "location": ase.get("location"),
            "sku": ase.get("kind"),
            "tags": json.dumps(ase.get("tags") or {}),
            "is_public": is_public,
            "fqdn": dns_suffix,
            "pipeline_tag": None,
            "raw_json": json.dumps({**ase, "_extra": extra}),
        })

    return results
