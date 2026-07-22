from __future__ import annotations

import json
from typing import Callable


SUBSCRIPTION_DRILLABLE_ARM_TYPES = {
    "microsoft.network/virtualnetworks",
    "microsoft.network/virtualnetworks/subnets",
    "microsoft.network/applicationgateways",
    "microsoft.apimanagement/service",
    "microsoft.appconfiguration/configurationstores",
    "microsoft.keyvault/vaults",
    "microsoft.managedidentity/userassignedidentities",
    "microsoft.storage/storageaccounts",
    "microsoft.sql/servers",
    "microsoft.containerservice/managedclusters",
    "microsoft.documentdb/databaseaccounts",
    "microsoft.machinelearningservices/workspaces",
    "microsoft.web/sites",
    "microsoft.web/serverfarms",
    "microsoft.web/hostingenvironments",
    "microsoft.servicefabric/clusters",
    "microsoft.servicebus/namespaces",
    "microsoft.logic/workflows",
    "microsoft.eventgrid/topics",
    "microsoft.kusto/clusters",
    "microsoft.databricks/workspaces",
    "microsoft.network/firewallpolicies",
}

SUBSCRIPTION_FQDN_SUFFIXES = {
    "microsoft.apimanagement/service": "azure-api.net",
    "microsoft.keyvault/vaults": "vault.azure.net",
    "microsoft.storage/storageaccounts": "blob.core.windows.net",
    "microsoft.servicebus/namespaces": "servicebus.windows.net",
    "microsoft.eventhub/namespaces": "servicebus.windows.net",
    "microsoft.sql/servers": "database.windows.net",
    "microsoft.cache/redis": "redis.cache.windows.net",
    "microsoft.documentdb/databaseaccounts": "documents.azure.com",
    "microsoft.appconfiguration/configurationstores": "azconfig.io",
}


def subscription_node_id(item: dict, sanitise_node_id: Callable[[str], str]) -> str:
    rg = item.get("rg") or "grp"
    type_key = (item.get("arm_type") or "").lower()
    if item.get("parent_vnet_name") and any(token in type_key for token in ("subnet", "hostingenvironment", "serverfarms", "servicefabric")):
        combined = f"{rg}_{item.get('parent_vnet_name')}_{item.get('name') or item.get('label') or 'resource'}"
    else:
        combined = f"{rg}_{item.get('name') or item.get('label') or 'resource'}"
    return sanitise_node_id(combined)


def subscription_short_name(name: str, max_len: int = 28) -> str:
    import re

    for prefix in ("production-", "prod-", "subscription-production-"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    name = re.sub(r"-(uksouth|ukwest|eastus\d*|westeurope|northeurope|westus\d*)$", "", name, flags=re.IGNORECASE)
    if len(name) > max_len:
        name = name[: max_len - 1] + "..."
    return name


def subscription_is_function_app(item: dict) -> bool:
    name = (item.get("name") or "").lower()
    return "-fn-" in name or name.endswith("-fn") or "funcapp" in name or "functionapp" in name


def subscription_known_fqdn_suffix(arm_type: str) -> str | None:
    return SUBSCRIPTION_FQDN_SUFFIXES.get((arm_type or "").lower())


def subscription_asset_tier(arm_type: str, name: str = "") -> str:
    type_key = (arm_type or "").lower()
    item = {"name": name}
    if (
        "applicationgateway" in type_key
        or "frontdoor" in type_key
        or "publicipaddress" in type_key
        or "trafficmanager" in type_key
        or "cdn/profiles" in type_key
        or "network/loadbalancers" in type_key
        or "bastionhost" in type_key
        or "azurefirewalls" in type_key
    ):
        return "entry"
    if "apimanagement" in type_key:
        return "api"
    if "kubernetes_ingress" in type_key or "microsoft.kubernetes/ingresses" in type_key:
        return "api"
    if (
        "virtualnetwork" in type_key
        or "/subnets" in type_key
        or "networksecuritygroup" in type_key
        or "routetable" in type_key
        or "privateendpoint" in type_key
        or "privatednszones" in type_key
        or "privatednszone" in type_key
        or "hostingenvironment" in type_key
    ):
        return "network"
    if (
        "managedcluster" in type_key
        or "containerinstance" in type_key
        or "serverfarms" in type_key
        or "datafactory" in type_key
        or "cognitiveservices" in type_key
        or "machinelearningservices" in type_key
        or "containerregistry" in type_key
        or "servicefabric" in type_key
        or "logic/workflows" in type_key
        or "eventgrid/topics" in type_key
        or "databricks/workspaces" in type_key
        # insights/components intentionally excluded — App Insights is a monitoring
        # sink, not a compute backend; classifying it as backend implies it routes traffic
    ):
        return "backend"
    if "sites" in type_key:
        return "backend" if not subscription_is_function_app(item) else "backend"
    if "virtualmachinescalesets" in type_key:
        return "backend"
    if "kubernetes_service" in type_key or "microsoft.kubernetes/services" in type_key:
        return "backend"
    if (
        "sql" in type_key
        or "documentdb" in type_key
        or "storage" in type_key
        or "keyvault" in type_key
        or "managedidentity/userassignedidentities" in type_key
        or "servicebus" in type_key
        or "eventhub" in type_key
        or "cache/redis" in type_key
        or "search/search" in type_key
        or "appconfiguration" in type_key
        or "kusto/clusters" in type_key
    ):
        return "data"
    return "other"


def subscription_is_kubernetes_service(asset: dict) -> bool:
    type_key = (asset.get("arm_type") or asset.get("type") or "").lower()
    return "kubernetes_service" in type_key or "microsoft.kubernetes/services" in type_key


def subscription_should_hide_in_subscription_diagram(asset: dict) -> bool:
    return subscription_is_kubernetes_service(asset)


def subscription_assets_from_rows(rows: list, friendly_type: Callable[[str], str]) -> list[dict]:
    def _parse_json(value: object) -> dict | list | None:
        if value is None or value == "":
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

    def _extract_subnet_ids(parsed_raw_json: dict) -> list[str]:
        def _add(values: list[str], candidate: object) -> None:
            sid = str(candidate or "").strip()
            if sid and "/subnets/" in sid and sid not in values:
                values.append(sid)

        found: list[str] = []
        if not isinstance(parsed_raw_json, dict):
            return found

        candidates = [parsed_raw_json]
        props = parsed_raw_json.get("properties")
        if isinstance(props, dict):
            candidates.append(props)

        for obj in candidates:
            if not isinstance(obj, dict):
                continue

            _add(found, obj.get("subnetId"))
            _add(found, obj.get("subnet_id"))
            _add(found, obj.get("virtualNetworkSubnetId"))
            _add(found, obj.get("virtual_network_subnet_id"))

            subnet_obj = obj.get("subnet")
            if isinstance(subnet_obj, dict):
                _add(found, subnet_obj.get("id"))

            site_cfg = obj.get("siteConfig")
            if isinstance(site_cfg, dict):
                _add(found, site_cfg.get("virtualNetworkSubnetId"))
                _add(found, site_cfg.get("virtual_network_subnet_id"))
                _add(found, site_cfg.get("subnetId"))
                _add(found, site_cfg.get("subnet_id"))
                site_subnet = site_cfg.get("subnet")
                if isinstance(site_subnet, dict):
                    _add(found, site_subnet.get("id"))

            vnet_cfg = obj.get("virtualNetworkConfiguration")
            if isinstance(vnet_cfg, dict):
                _add(found, vnet_cfg.get("subnetResourceId"))
                _add(found, vnet_cfg.get("subnetResourceID"))
                subnet_obj = vnet_cfg.get("subnet")
                if isinstance(subnet_obj, dict):
                    _add(found, subnet_obj.get("id"))

            net_profile = obj.get("networkProfile")
            if isinstance(net_profile, dict):
                _add(found, net_profile.get("subnetId"))
                _add(found, net_profile.get("subnet_id"))

            vm_profile = obj.get("virtualMachineProfile")
            if isinstance(vm_profile, dict):
                vm_net_profile = vm_profile.get("networkProfile")
                if isinstance(vm_net_profile, dict):
                    for nic_cfg in vm_net_profile.get("networkInterfaceConfigurations") or []:
                        if not isinstance(nic_cfg, dict):
                            continue
                        nic_props = nic_cfg.get("properties") or {}
                        ip_configs = list(nic_cfg.get("ipConfigurations") or [])
                        if isinstance(nic_props, dict):
                            ip_configs.extend(nic_props.get("ipConfigurations") or [])
                        for ip_cfg in ip_configs:
                            if not isinstance(ip_cfg, dict):
                                continue
                            sub = ip_cfg.get("subnet")
                            if isinstance(sub, dict):
                                _add(found, sub.get("id"))
                            ip_props = ip_cfg.get("properties") or {}
                            if isinstance(ip_props, dict):
                                sub = ip_props.get("subnet")
                                if isinstance(sub, dict):
                                    _add(found, sub.get("id"))

            _props = obj if not isinstance(props, dict) else props
            virtual_network_profile = _props.get("virtualNetworkProfile")
            if isinstance(virtual_network_profile, dict):
                subnet_obj = virtual_network_profile.get("subnet")
                if isinstance(subnet_obj, dict):
                    _add(found, subnet_obj.get("id"))
                _add(found, virtual_network_profile.get("subnetId"))
                _add(found, virtual_network_profile.get("subnet_id"))

            for node_type in _props.get("nodeTypes") or []:
                if not isinstance(node_type, dict):
                    continue
                _add(found, node_type.get("subnetId"))
                _add(found, node_type.get("subnet_id"))
                _add(found, node_type.get("vnetSubnetID"))
                _add(found, node_type.get("vnetSubnetId"))
                _add(found, node_type.get("virtualNetworkSubnetId"))
                _add(found, node_type.get("virtual_network_subnet_id"))
                subnet_obj = node_type.get("subnet")
                if isinstance(subnet_obj, dict):
                    _add(found, subnet_obj.get("id"))

            for pool in obj.get("agentPoolProfiles") or []:
                if not isinstance(pool, dict):
                    continue
                _add(found, pool.get("vnetSubnetID"))
                _add(found, pool.get("vnetSubnetId"))
                _add(found, pool.get("subnetId"))
                _add(found, pool.get("subnet_id"))

            for ip_cfg in obj.get("ipConfigurations") or []:
                if not isinstance(ip_cfg, dict):
                    continue
                sub = ip_cfg.get("subnet")
                if isinstance(sub, dict):
                    _add(found, sub.get("id"))
                ip_props = ip_cfg.get("properties") or {}
                if isinstance(ip_props, dict):
                    sub = ip_props.get("subnet")
                    if isinstance(sub, dict):
                        _add(found, sub.get("id"))

            for gw_cfg in obj.get("gatewayIPConfigurations") or []:
                if not isinstance(gw_cfg, dict):
                    continue
                sub = gw_cfg.get("subnet")
                if isinstance(sub, dict):
                    _add(found, sub.get("id"))
                gw_props = gw_cfg.get("properties") or {}
                if isinstance(gw_props, dict):
                    sub = gw_props.get("subnet")
                    if isinstance(sub, dict):
                        _add(found, sub.get("id"))

            for fe_cfg in obj.get("frontendIPConfigurations") or []:
                if not isinstance(fe_cfg, dict):
                    continue
                sub = fe_cfg.get("subnet")
                if isinstance(sub, dict):
                    _add(found, sub.get("id"))
                fe_props = fe_cfg.get("properties") or {}
                if isinstance(fe_props, dict):
                    sub = fe_props.get("subnet")
                    if isinstance(sub, dict):
                        _add(found, sub.get("id"))

        return found

    def _extract_public_ip_ids(parsed_raw_json: dict) -> list[str]:
        found: list[str] = []
        if not isinstance(parsed_raw_json, dict):
            return found

        def _add(candidate: object) -> None:
            pid = str(candidate or "").strip()
            if pid and "/publicipaddresses/" in pid.lower() and pid not in found:
                found.append(pid)

        def _visit(value: object) -> None:
            if isinstance(value, dict):
                for key, inner in value.items():
                    key_l = str(key).lower()
                    if key_l in {"id", "resourceid"} and isinstance(inner, str):
                        _add(inner)
                    if key_l in {"publicipaddress", "publicipaddressid", "publicipaddress_id", "publicipaddressresourceid"}:
                        if isinstance(inner, str):
                            _add(inner)
                        elif isinstance(inner, dict):
                            _add(inner.get("id"))
                    _visit(inner)
            elif isinstance(value, list):
                for item in value:
                    _visit(item)
            elif isinstance(value, str):
                _add(value)

        _visit(parsed_raw_json)

        return found

    def _extract_subnet_id(parsed_raw_json: dict) -> str | None:
        subnet_ids = _extract_subnet_ids(parsed_raw_json)
        return subnet_ids[0] if subnet_ids else None

    assets: list[dict] = []
    public_ip_assets_by_id: dict[str, dict] = {}
    public_ip_assets_by_name_rg: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        name, rtype, rg, fqdn, is_public, sku = row[:6]
        asset_id = row[6] if len(row) > 6 else None
        has_waf = bool(row[7]) if len(row) > 7 else False
        listeners = row[8] if len(row) > 8 else None
        is_restricted = bool(row[9]) if len(row) > 9 else False
        waf_mode = row[10] if len(row) > 10 else None
        routing_targets = row[11] if len(row) > 11 else None
        raw_json = row[12] if len(row) > 12 else None
        auth_methods_raw = row[13] if len(row) > 13 else None
        parsed_raw = _parse_json(raw_json)
        if isinstance(auth_methods_raw, str):
            try:
                auth_methods = json.loads(auth_methods_raw)
                if not isinstance(auth_methods, list):
                    auth_methods = []
            except Exception:
                auth_methods = []
        elif isinstance(auth_methods_raw, list):
            auth_methods = auth_methods_raw
        else:
            auth_methods = []

        asset = {
            "name": name,
            "arm_type": rtype,
            "rg": rg or "default",
            "fqdn": fqdn or "",
            "public": bool(is_public),
            "sku": sku,
            "id": asset_id,
            "has_waf": has_waf,
            "waf_mode": waf_mode,
            "listeners": listeners,
            "routing_targets": routing_targets,
            "is_restricted": is_restricted,
            "tier": subscription_asset_tier(rtype, name),
            "friendly_type": friendly_type(rtype),
            "short_name": subscription_short_name(name or "resource"),
            "auth_methods": auth_methods,
        }
        if (
            not asset.get("routing_targets")
            and str(rtype or "").strip().lower() == "apim backend target"
            and isinstance(parsed_raw, dict)
        ):
            backend_target = (
                parsed_raw.get("target_resource_id")
                or parsed_raw.get("resource_id")
                or parsed_raw.get("backend_url")
                or parsed_raw.get("backend_target")
                or parsed_raw.get("target")
                or parsed_raw.get("hostname")
                or parsed_raw.get("name")
            )
            if backend_target:
                asset["routing_targets"] = [{
                    "target": str(backend_target).strip(),
                    "name": str(parsed_raw.get("backend_id") or parsed_raw.get("name") or "").strip(),
                    "target_resource_id": str(parsed_raw.get("target_resource_id") or "").strip(),
                }]
        if isinstance(parsed_raw, dict):
            public_ip_resource_ids = _extract_public_ip_ids(parsed_raw)
            if public_ip_resource_ids:
                asset["public_ip_resource_ids"] = public_ip_resource_ids
            props = parsed_raw.get("properties")
            if isinstance(props, dict):
                for candidate in (
                    props.get("publicIpAddress"),
                    props.get("ipAddress"),
                ):
                    if isinstance(candidate, str) and candidate.strip():
                        asset["public_ip"] = candidate.strip()
                        asset["public_ips"] = [candidate.strip()]
                        break
                    if isinstance(candidate, dict):
                        ip_addr = candidate.get("ipAddress") or candidate.get("address")
                        if isinstance(ip_addr, str) and ip_addr.strip():
                            asset["public_ip"] = ip_addr.strip()
                            asset["public_ips"] = [ip_addr.strip()]
                            break
                for candidate in (
                    props.get("publicIpAddress"),
                    props.get("ipAddress"),
                ):
                    if isinstance(candidate, str) and candidate.strip():
                        asset["public_ip"] = candidate.strip()
                        break
                    if isinstance(candidate, dict):
                        ip_addr = candidate.get("ipAddress") or candidate.get("address")
                        if isinstance(ip_addr, str) and ip_addr.strip():
                            asset["public_ip"] = ip_addr.strip()
                            break
                if "serverfarms" in (rtype or "").lower() and not asset.get("subnet_id") and not asset.get("parent_vnet_name"):
                    hosting_profile = props.get("hostingEnvironmentProfile") or props.get("hosting_environment_profile")
                    if hosting_profile is None:
                        hosting_profile = parsed_raw.get("hostingEnvironmentProfile") or parsed_raw.get("hosting_environment_profile")
                    hosting_profile_id = ""
                    if isinstance(hosting_profile, dict):
                        hosting_profile_id = str(hosting_profile.get("id") or "").strip()
                    elif isinstance(hosting_profile, str):
                        hosting_profile_id = hosting_profile.strip()

                    candidate_ase = None
                    if hosting_profile_id:
                        hp_lower = hosting_profile_id.lower()
                        for existing in assets:
                            if "hostingenvironment" not in (existing.get("arm_type") or "").lower():
                                continue
                            if str(existing.get("id") or "").strip().lower() == hp_lower:
                                candidate_ase = existing
                                break
                        if candidate_ase is None:
                            host_name = hp_lower.rsplit("/", 1)[-1]
                            host_rg = ""
                            if "/resourcegroups/" in hp_lower:
                                host_rg = hp_lower.split("/resourcegroups/", 1)[1].split("/providers/", 1)[0].split("/")[0]
                            for existing in assets:
                                if "hostingenvironment" not in (existing.get("arm_type") or "").lower():
                                    continue
                                if host_name and str(existing.get("name") or "").strip().lower() != host_name:
                                    continue
                                existing_rg = str(existing.get("rg") or "").strip().lower()
                                if host_rg and existing_rg and existing_rg != host_rg:
                                    continue
                                candidate_ase = existing
                                break

                    if candidate_ase:
                        asset["vnet_name"] = candidate_ase.get("vnet_name") or candidate_ase.get("parent_vnet_name")
                        asset["parent_vnet_name"] = candidate_ase.get("parent_vnet_name") or candidate_ase.get("vnet_name")
                        asset["vnet_resource_group"] = candidate_ase.get("vnet_resource_group") or candidate_ase.get("parent_vnet_resource_group")
                        asset["parent_vnet_resource_group"] = candidate_ase.get("parent_vnet_resource_group") or candidate_ase.get("vnet_resource_group")
                        asset["subnet_name"] = candidate_ase.get("subnet_name")
                        asset["subnet_id"] = candidate_ase.get("subnet_id")
                        asset["address_prefix"] = candidate_ase.get("address_prefix")
                        asset["address_prefixes"] = list(candidate_ase.get("address_prefixes") or [])
                        asset["network_security_group_id"] = candidate_ase.get("network_security_group_id")
                        asset["network_security_group_name"] = candidate_ase.get("network_security_group_name")
                        asset["route_table_id"] = candidate_ase.get("route_table_id")
                        asset["route_table_name"] = candidate_ase.get("route_table_name")
                        asset["delegations"] = list(candidate_ase.get("delegations") or [])
                        asset["network"] = dict(candidate_ase.get("network") or {})

            if "serverfarms" in (rtype or "").lower() and not asset.get("subnet_id") and not asset.get("parent_vnet_name"):
                hosting_profile = None
                if isinstance(props, dict):
                    hosting_profile = props.get("hostingEnvironmentProfile") or props.get("hosting_environment_profile")
                if hosting_profile is None:
                    hosting_profile = parsed_raw.get("hostingEnvironmentProfile") or parsed_raw.get("hosting_environment_profile")
                hosting_profile_id = ""
                if isinstance(hosting_profile, dict):
                    hosting_profile_id = str(hosting_profile.get("id") or "").strip()
                elif isinstance(hosting_profile, str):
                    hosting_profile_id = hosting_profile.strip()

                candidate_ase = None
                if hosting_profile_id:
                    hp_lower = hosting_profile_id.lower()
                    for existing in assets:
                        if "hostingenvironment" not in (existing.get("arm_type") or "").lower():
                            continue
                        if str(existing.get("id") or "").strip().lower() == hp_lower:
                            candidate_ase = existing
                            break
                    if candidate_ase is None:
                        host_name = hp_lower.rsplit("/", 1)[-1]
                        host_rg = ""
                        if "/resourcegroups/" in hp_lower:
                            host_rg = hp_lower.split("/resourcegroups/", 1)[1].split("/providers/", 1)[0].split("/")[0]
                        for existing in assets:
                            if "hostingenvironment" not in (existing.get("arm_type") or "").lower():
                                continue
                            if host_name and str(existing.get("name") or "").strip().lower() != host_name:
                                continue
                            existing_rg = str(existing.get("rg") or "").strip().lower()
                            if host_rg and existing_rg and existing_rg != host_rg:
                                continue
                            candidate_ase = existing
                            break

                if candidate_ase:
                    asset["vnet_name"] = candidate_ase.get("vnet_name") or candidate_ase.get("parent_vnet_name")
                    asset["parent_vnet_name"] = candidate_ase.get("parent_vnet_name") or candidate_ase.get("vnet_name")
                    asset["vnet_resource_group"] = candidate_ase.get("vnet_resource_group") or candidate_ase.get("parent_vnet_resource_group")
                    asset["parent_vnet_resource_group"] = candidate_ase.get("parent_vnet_resource_group") or candidate_ase.get("vnet_resource_group")
                    asset["subnet_name"] = candidate_ase.get("subnet_name")
                    asset["subnet_id"] = candidate_ase.get("subnet_id")
                    asset["address_prefix"] = candidate_ase.get("address_prefix")
                    asset["address_prefixes"] = list(candidate_ase.get("address_prefixes") or [])
                    asset["network_security_group_id"] = candidate_ase.get("network_security_group_id")
                    asset["network_security_group_name"] = candidate_ase.get("network_security_group_name")
                    asset["route_table_id"] = candidate_ase.get("route_table_id")
                    asset["route_table_name"] = candidate_ase.get("route_table_name")
                    asset["delegations"] = list(candidate_ase.get("delegations") or [])
                    asset["network"] = dict(candidate_ase.get("network") or {})

            subnet_id = _extract_subnet_id(parsed_raw)
            if not subnet_id and "virtualnetworks/subnets" in (rtype or "").lower():
                asset_id_text = str(asset.get("id") or "").strip()
                if "/subnets/" in asset_id_text.lower():
                    subnet_id = asset_id_text
            if subnet_id:
                asset["subnet_id"] = subnet_id
                if not asset.get("subnet_name"):
                    asset["subnet_name"] = subnet_id.split("/subnets/")[-1] if "/subnets/" in subnet_id.lower() else None
                if not asset.get("vnet_name"):
                    vnet_name = None
                    if "/virtualnetworks/" in subnet_id.lower():
                        vnet_name = subnet_id.split("/virtualNetworks/", 1)[1].split("/")[0]
                    if vnet_name:
                        asset["vnet_name"] = vnet_name
                        asset["parent_vnet_name"] = vnet_name
                if not asset.get("parent_vnet_id") and "/subnets/" in subnet_id.lower():
                    asset["parent_vnet_id"] = subnet_id.rsplit("/subnets/", 1)[0]
            extra = parsed_raw.get("_extra") or {}
            if isinstance(extra, dict):
                if extra.get("subnet_id"):
                    asset["subnet_id"] = extra.get("subnet_id")
                if extra.get("subnet_ids"):
                    asset["subnet_ids"] = extra.get("subnet_ids") or []
                if extra.get("vnet_name"):
                    asset["vnet_name"] = extra.get("vnet_name")
                    asset["parent_vnet_name"] = extra.get("vnet_name")
                if extra.get("subnet_name"):
                    asset["subnet_name"] = extra.get("subnet_name")
                if extra.get("vnet_resource_group"):
                    asset["vnet_resource_group"] = extra.get("vnet_resource_group")
                    asset["parent_vnet_resource_group"] = extra.get("vnet_resource_group")
                if extra.get("parent_vnet_id"):
                    asset["parent_vnet_id"] = extra.get("parent_vnet_id")
                    asset["parent_vnet_name"] = extra.get("parent_vnet_name")
                    asset["parent_vnet_resource_group"] = extra.get("parent_vnet_resource_group")
                    asset["address_prefix"] = extra.get("address_prefix")
                    asset["address_prefixes"] = extra.get("address_prefixes") or []
                    asset["network_security_group_id"] = extra.get("network_security_group_id")
                    asset["network_security_group_name"] = extra.get("network_security_group_name")
                    asset["route_table_id"] = extra.get("route_table_id")
                    asset["route_table_name"] = extra.get("route_table_name")
                    asset["delegations"] = extra.get("delegations") or []
                if extra.get("slot_parent"):
                    asset["parent_name"] = extra.get("slot_parent")
                    asset["parent_resource_group"] = rg or "default"
                    kind_lc = str((parsed_raw or {}).get("kind") or "").lower()
                    asset["parent_type_label"] = "Function App" if "functionapp" in kind_lc or "function app" in kind_lc else "App Service"
                if asset.get("subnet_id") and not asset.get("subnet_name"):
                    asset["subnet_name"] = asset["subnet_id"].split("/subnets/")[-1] if "/subnets/" in str(asset["subnet_id"]) else None
                if asset.get("subnet_id") and not asset.get("vnet_name"):
                    parts = str(asset["subnet_id"]).split("/virtualNetworks/")
                    if len(parts) > 1:
                        asset["vnet_name"] = parts[1].split("/")[0]
                        asset["parent_vnet_name"] = asset["vnet_name"]
                if not asset.get("subnet_id"):
                    subnet_ids = _extract_subnet_ids(parsed_raw)
                    if subnet_ids:
                        asset["subnet_id"] = subnet_ids[0]
                if asset.get("subnet_id") and not asset.get("subnet_name"):
                    asset["subnet_name"] = asset["subnet_id"].split("/subnets/")[-1] if "/subnets/" in str(asset["subnet_id"]) else None
                if asset.get("subnet_id") and not asset.get("vnet_name"):
                    parts = str(asset["subnet_id"]).split("/virtualNetworks/")
                    if len(parts) > 1:
                        asset["vnet_name"] = parts[1].split("/")[0]
                        asset["parent_vnet_name"] = asset["vnet_name"]
                elif (rtype or "").lower().endswith("/virtualnetworks"):
                    subnets = extra.get("subnets") or []
                    for subnet in subnets:
                        if not isinstance(subnet, dict):
                            continue
                        subnet_name = subnet.get("name")
                        subnet_id = subnet.get("id") or (
                            f"{asset_id}/subnets/{subnet_name}" if asset_id and subnet_name else None
                        )
                        subnet_props = subnet.get("properties") or {}
                        if not subnet_name or not subnet_id:
                            continue
                        assets.append({
                            "name": subnet_name,
                            "arm_type": "Microsoft.Network/virtualNetworks/subnets",
                            "rg": rg or "default",
                            "fqdn": "",
                            "public": False,
                            "sku": None,
                            "id": subnet_id,
                            "has_waf": False,
                            "waf_mode": None,
                            "listeners": None,
                            "routing_targets": None,
                            "is_restricted": bool(subnet_props.get("networkSecurityGroup") or subnet_props.get("routeTable")),
                            "tier": "network",
                            "friendly_type": friendly_type("Microsoft.Network/virtualNetworks/subnets"),
                            "short_name": subscription_short_name(subnet_name),
                            "parent_vnet_id": asset_id,
                            "parent_vnet_name": name,
                            "parent_vnet_resource_group": rg or "default",
                            "resources": [
                                {"rg": rg or "default", "name": name},
                                {"rg": rg or "default", "name": subnet_name},
                            ],
                            "address_prefix": subnet_props.get("addressPrefix"),
                            "address_prefixes": subnet_props.get("addressPrefixes") or [],
                            "network_security_group_id": (subnet_props.get("networkSecurityGroup") or {}).get("id"),
                            "network_security_group_name": (subnet_props.get("networkSecurityGroup") or {}).get("name"),
                            "route_table_id": (subnet_props.get("routeTable") or {}).get("id"),
                            "route_table_name": (subnet_props.get("routeTable") or {}).get("name"),
                            "delegations": [
                                (d.get("properties") or {}).get("serviceName")
                                for d in subnet_props.get("delegations") or []
                                if (d.get("properties") or {}).get("serviceName")
                            ],
                            "auth_methods": [],
                        })
        resolved_fqdn = subscription_primary_fqdn(asset)
        asset["fqdn"] = resolved_fqdn
        asset["fqdns"] = [resolved_fqdn] if resolved_fqdn else []
        if "publicipaddresses" in (rtype or "").lower():
            if asset_id:
                public_ip_assets_by_id[str(asset_id).strip().lower()] = asset
            name_key = str(name or "").strip().lower()
            rg_key = str(rg or "").strip().lower()
            if name_key:
                public_ip_assets_by_name_rg.setdefault((rg_key, name_key), []).append(asset)
        assets.append(asset)

    def _is_apim_public_ip_asset(asset: dict) -> bool:
        type_key = (asset.get("arm_type") or "").lower()
        if "publicipaddresses" not in type_key:
            return False
        if asset.get("collapse_into_apim") or asset.get("parent_apim_key"):
            return True
        name_key = str(asset.get("name") or "").strip().lower()
        return "apim" in name_key or name_key.startswith("api-management")

    linked_public_ip_ids: set[str] = set()
    for asset in assets:
        if "apimanagement" not in (asset.get("arm_type") or "").lower():
            continue
        linked_assets: list[dict] = []
        for pip_id in asset.get("public_ip_resource_ids") or []:
            pip_key = str(pip_id or "").strip().lower()
            if not pip_key:
                continue
            candidate = public_ip_assets_by_id.get(pip_key)
            if candidate:
                linked_assets.append(candidate)
                linked_public_ip_ids.add(pip_key)
        if not linked_assets:
            name_key = str(asset.get("name") or "").strip().lower()
            rg_key = str(asset.get("rg") or "").strip().lower()
            linked_assets = list(public_ip_assets_by_name_rg.get((rg_key, name_key), []))
            for candidate in linked_assets:
                candidate_id = str(candidate.get("id") or "").strip().lower()
                if candidate_id:
                    linked_public_ip_ids.add(candidate_id)
        if linked_assets:
            linked_ips: list[str] = []
            seen_ips: set[str] = set()
            for pip in linked_assets:
                for ip in pip.get("public_ips") or []:
                    ip_norm = str(ip or "").strip()
                    if ip_norm and ip_norm not in seen_ips:
                        seen_ips.add(ip_norm)
                        linked_ips.append(ip_norm)
            if linked_ips:
                asset["associated_public_ips"] = linked_ips
                asset["public_ips"] = list(dict.fromkeys([*(asset.get("public_ips") or []), *linked_ips]))
                if not asset.get("public_ip"):
                    asset["public_ip"] = linked_ips[0]
            for pip in linked_assets:
                pip["collapse_into_apim"] = True
                pip["parent_apim_key"] = ((asset.get("rg") or "").lower(), (asset.get("name") or "").lower())

    def _infer_ase_name_from_fqdn(fqdn: str) -> str | None:
        host = str(fqdn or "").strip().lower()
        suffix = ".appserviceenvironment.net"
        if not host.endswith(suffix):
            return None
        host = host[: -len(suffix)]
        if not host or "." not in host:
            return None
        return host.rsplit(".", 1)[-1]

    def _is_ase(asset: dict) -> bool:
        return "hostingenvironment" in (asset.get("arm_type") or "").lower()

    ase_assets_by_key: dict[tuple[str, str], dict] = {}
    ase_assets_by_name: dict[str, list[dict]] = {}
    for asset in assets:
        if not _is_ase(asset):
            continue
        key = ((asset.get("rg") or "").lower(), (asset.get("name") or "").lower())
        ase_assets_by_key[key] = asset
        ase_assets_by_name.setdefault((asset.get("name") or "").lower(), []).append(asset)

    ase_network_fields = (
        "parent_vnet_id",
        "parent_vnet_name",
        "parent_vnet_resource_group",
        "vnet_name",
        "vnet_resource_group",
        "subnet_id",
        "subnet_name",
        "address_prefix",
        "address_prefixes",
        "network_security_group_id",
        "network_security_group_name",
        "route_table_id",
        "route_table_name",
        "delegations",
    )

    for asset in assets:
        type_key = (asset.get("arm_type") or "").lower()
        if "serverfarms" not in type_key:
            continue
        if asset.get("subnet_id") or asset.get("parent_vnet_name"):
            continue

        candidate_ase_name = None
        for fqdn in asset.get("fqdns") or []:
            candidate_ase_name = _infer_ase_name_from_fqdn(fqdn)
            if candidate_ase_name:
                break
        if not candidate_ase_name:
            continue

        candidate_ase = ase_assets_by_key.get(((asset.get("rg") or "").lower(), candidate_ase_name))
        if candidate_ase is None:
            matches = ase_assets_by_name.get(candidate_ase_name, [])
            candidate_ase = matches[0] if matches else None
        if not candidate_ase:
            continue

        if not asset.get("vnet_name"):
            asset["vnet_name"] = candidate_ase.get("vnet_name") or candidate_ase.get("parent_vnet_name")
        if not asset.get("vnet_resource_group"):
            asset["vnet_resource_group"] = candidate_ase.get("vnet_resource_group") or candidate_ase.get("parent_vnet_resource_group")
        if not asset.get("subnet_name"):
            asset["subnet_name"] = candidate_ase.get("subnet_name")
        if not asset.get("subnet_id"):
            asset["subnet_id"] = candidate_ase.get("subnet_id")
        for field in ase_network_fields:
            if candidate_ase.get(field) and not asset.get(field):
                asset[field] = candidate_ase.get(field)
        if candidate_ase.get("parent_vnet_name") and not asset.get("parent_vnet_name"):
            asset["parent_vnet_name"] = candidate_ase.get("parent_vnet_name")
        if candidate_ase.get("parent_vnet_resource_group") and not asset.get("parent_vnet_resource_group"):
            asset["parent_vnet_resource_group"] = candidate_ase.get("parent_vnet_resource_group")
        if candidate_ase.get("parent_vnet_id") and not asset.get("parent_vnet_id"):
            asset["parent_vnet_id"] = candidate_ase.get("parent_vnet_id")

    visible_assets: list[dict] = []
    for asset in assets:
        if _is_apim_public_ip_asset(asset):
            continue
        visible_assets.append(asset)
    return visible_assets


def subscription_apply_plan_hierarchy(assets: list[dict], plan_links: list | None = None) -> list[dict]:
    """Fold hosted App Services / Function Apps into their hosting parent.

    The returned list keeps App Service Plans and App Service Environments visible,
    hides hosted sites that have a matching parent in scope, and aggregates the
    hosted sites' FQDN/public exposure onto the parent node so the diagram stays
    clickable and accurate.
    """
    from collections import defaultdict

    if not plan_links:
        return [dict(asset) for asset in assets]

    def _key(asset: dict) -> tuple[str, str]:
        return ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower())

    def _type(asset: dict) -> str:
        return (asset.get("arm_type") or asset.get("type") or "").lower()

    def _is_plan_type(asset: dict) -> bool:
        return any(token in _type(asset) for token in ("serverfarms", "hostingenvironment"))

    def _is_site_type(asset: dict) -> bool:
        return "sites" in _type(asset)

    def _first_value(*values: object) -> object:
        for value in values:
            if value not in (None, "", [], {}):
                return value
        return None

    def _merge_list_values(*values: object) -> list:
        merged_values: list = []
        for value in values:
            if not value:
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                if item not in merged_values:
                    merged_values.append(item)
        return merged_values

    def _merge_asset_payload(base: dict, extra: dict) -> dict:
        merged = dict(base)
        merged_resources = list(merged.get("resources") or [])
        for resource in extra.get("resources") or []:
            if resource not in merged_resources:
                merged_resources.append(resource)
        if merged_resources:
            merged["resources"] = merged_resources

        merged_fqdns = list(dict.fromkeys([*(merged.get("fqdns") or []), *(extra.get("fqdns") or [])]))
        if merged_fqdns:
            merged["fqdns"] = merged_fqdns

        merged["public"] = bool(merged.get("public") or extra.get("public"))
        merged["is_restricted"] = bool(merged.get("is_restricted") or extra.get("is_restricted"))
        if not merged.get("public_ip") and extra.get("public_ip"):
            merged["public_ip"] = extra.get("public_ip")

        merged["vnet_name"] = _first_value(merged.get("vnet_name"), extra.get("vnet_name"), merged.get("parent_vnet_name"), extra.get("parent_vnet_name"))
        merged["parent_vnet_name"] = _first_value(merged.get("parent_vnet_name"), extra.get("parent_vnet_name"), merged.get("vnet_name"), extra.get("vnet_name"))
        merged["vnet_resource_group"] = _first_value(merged.get("vnet_resource_group"), extra.get("vnet_resource_group"), merged.get("parent_vnet_resource_group"), extra.get("parent_vnet_resource_group"))
        merged["parent_vnet_resource_group"] = _first_value(
            merged.get("parent_vnet_resource_group"),
            extra.get("parent_vnet_resource_group"),
            merged.get("vnet_resource_group"),
            extra.get("vnet_resource_group"),
        )
        merged["subnet_name"] = _first_value(merged.get("subnet_name"), extra.get("subnet_name"))
        merged["subnet_id"] = _first_value(merged.get("subnet_id"), extra.get("subnet_id"))
        merged["address_prefix"] = _first_value(merged.get("address_prefix"), extra.get("address_prefix"))
        merged["address_prefixes"] = _merge_list_values(merged.get("address_prefixes"), extra.get("address_prefixes"))
        merged["network_security_group_id"] = _first_value(merged.get("network_security_group_id"), extra.get("network_security_group_id"))
        merged["network_security_group_name"] = _first_value(merged.get("network_security_group_name"), extra.get("network_security_group_name"))
        merged["route_table_id"] = _first_value(merged.get("route_table_id"), extra.get("route_table_id"))
        merged["route_table_name"] = _first_value(merged.get("route_table_name"), extra.get("route_table_name"))
        merged["delegations"] = _merge_list_values(merged.get("delegations"), extra.get("delegations"))
        if merged.get("vnet_name") or merged.get("subnet_name") or merged.get("subnet_id"):
            merged["network"] = {
                "vnet": _first_value(
                    (merged.get("network") or {}).get("vnet") if isinstance(merged.get("network"), dict) else None,
                    merged.get("vnet_name"),
                    extra.get("vnet_name"),
                    merged.get("parent_vnet_name"),
                    extra.get("parent_vnet_name"),
                ),
                "subnet": _first_value(
                    (merged.get("network") or {}).get("subnet") if isinstance(merged.get("network"), dict) else None,
                    merged.get("subnet_name"),
                    extra.get("subnet_name"),
                ),
                "vnet_resource_group": _first_value(
                    (merged.get("network") or {}).get("vnet_resource_group") if isinstance(merged.get("network"), dict) else None,
                    merged.get("vnet_resource_group"),
                    extra.get("vnet_resource_group"),
                    merged.get("parent_vnet_resource_group"),
                    extra.get("parent_vnet_resource_group"),
                ),
                "subnet_id": _first_value(
                    (merged.get("network") or {}).get("subnet_id") if isinstance(merged.get("network"), dict) else None,
                    merged.get("subnet_id"),
                    extra.get("subnet_id"),
                ),
            }

        if base is not extra and (_is_plan_type(base) or _is_plan_type(extra)) and (_is_site_type(base) or _is_site_type(extra)):
            merged["hosted_site_count"] = max(int(merged.get("hosted_site_count") or 0), int(extra.get("hosted_site_count") or 0), 1)
        return merged

    asset_map: dict[tuple[str, str], dict] = {}
    for asset in assets:
        key = _key(asset)
        existing = asset_map.get(key)
        if existing is None:
            asset_map[key] = dict(asset)
            continue
        existing_is_plan = _is_plan_type(existing)
        new_is_plan = _is_plan_type(asset)
        if new_is_plan and not existing_is_plan:
            asset_map[key] = _merge_asset_payload(asset, existing)
        else:
            asset_map[key] = _merge_asset_payload(existing, asset)
    hosted_by_parent: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for site_rg, site_name, plan_rg, plan_name in plan_links:
        site_key = ((site_name or "").lower(), (site_rg or "").lower())
        plan_key = ((plan_name or "").lower(), (plan_rg or "").lower())
        site_asset = asset_map.get(site_key)
        parent_asset = asset_map.get(plan_key)
        if site_key == plan_key:
            site_candidate = next(
                (asset for asset in assets if _key(asset) == site_key and _is_site_type(asset)),
                None,
            )
            parent_candidate = next(
                (asset for asset in assets if _key(asset) == plan_key and _is_plan_type(asset)),
                None,
            )
            if site_candidate:
                site_asset = site_candidate
            if parent_candidate:
                parent_asset = parent_candidate
        if not site_asset or not parent_asset:
            continue
        parent_type = _type(parent_asset)
        if "sites" not in _type(site_asset) or not any(
            token in parent_type for token in ("serverfarms", "hostingenvironment")
        ):
            continue
        hosted_by_parent[plan_key].append(site_asset)

    visible_assets: list[dict] = []
    for asset in assets:
        asset_copy = dict(asset)
        key = _key(asset_copy)
        children = hosted_by_parent.get(key)
        if children and _is_site_type(asset_copy):
            continue
        if children and any(token in _type(asset_copy) for token in ("serverfarms", "hostingenvironment")):
            is_ase = "hostingenvironment" in _type(asset_copy)
            # For App Service Environments, do NOT inherit child app FQDNs — routing
            # resolution should land on the App Service Plan (serverfarm), not the ASE
            # container.  Giving the ASE all app FQDNs causes node_by_fqdn to map every
            # app FQDN to the ASE node instead of the correct plan node.
            if not is_ase:
                child_fqdns = [subscription_primary_fqdn(child) for child in children if subscription_primary_fqdn(child)]
                merged_fqdns = list(dict.fromkeys([*(asset_copy.get("fqdns") or []), *child_fqdns]))
                asset_copy["fqdns"] = merged_fqdns
            asset_copy["public"] = bool(
                asset_copy.get("public")
                # For ASEs: inherit public exposure from hosted plans/sites — the ASE
                # is the container and may represent aggregated exposure in overview.
                # For regular App Service Plans (serverfarms): do NOT inherit public
                # from hosted sites.  App Services and Function Apps are always rendered
                # as individual nodes with their own Internet exposure arrows, so
                # propagating their public flag to the plan produces a duplicate
                # Internet → Plan arrow with a spurious HTTP/HTTPS label.
                or (is_ase and any(child.get("public") for child in children))
            )
            asset_copy["is_restricted"] = bool(asset_copy.get("is_restricted") or any(child.get("is_restricted") for child in children))
            asset_copy["hosted_site_count"] = len(children)
            child_networks = [child for child in children if child.get("vnet_name") or child.get("parent_vnet_name") or child.get("subnet_name") or child.get("subnet_id")]
            if child_networks:
                network_source = child_networks[0]
                asset_copy["vnet_name"] = _first_value(asset_copy.get("vnet_name"), asset_copy.get("parent_vnet_name"), network_source.get("vnet_name"), network_source.get("parent_vnet_name"))
                asset_copy["parent_vnet_name"] = _first_value(asset_copy.get("parent_vnet_name"), network_source.get("parent_vnet_name"), asset_copy.get("vnet_name"), network_source.get("vnet_name"))
                asset_copy["vnet_resource_group"] = _first_value(asset_copy.get("vnet_resource_group"), asset_copy.get("parent_vnet_resource_group"), network_source.get("vnet_resource_group"), network_source.get("parent_vnet_resource_group"))
                asset_copy["parent_vnet_resource_group"] = _first_value(asset_copy.get("parent_vnet_resource_group"), network_source.get("parent_vnet_resource_group"), asset_copy.get("vnet_resource_group"), network_source.get("vnet_resource_group"))
                asset_copy["subnet_name"] = _first_value(asset_copy.get("subnet_name"), network_source.get("subnet_name"))
                asset_copy["subnet_id"] = _first_value(asset_copy.get("subnet_id"), network_source.get("subnet_id"))
                asset_copy["address_prefix"] = _first_value(asset_copy.get("address_prefix"), network_source.get("address_prefix"))
                asset_copy["address_prefixes"] = _merge_list_values(asset_copy.get("address_prefixes"), network_source.get("address_prefixes"))
                asset_copy["network_security_group_id"] = _first_value(asset_copy.get("network_security_group_id"), network_source.get("network_security_group_id"))
                asset_copy["network_security_group_name"] = _first_value(asset_copy.get("network_security_group_name"), network_source.get("network_security_group_name"))
                asset_copy["route_table_id"] = _first_value(asset_copy.get("route_table_id"), network_source.get("route_table_id"))
                asset_copy["route_table_name"] = _first_value(asset_copy.get("route_table_name"), network_source.get("route_table_name"))
                asset_copy["delegations"] = _merge_list_values(asset_copy.get("delegations"), network_source.get("delegations"))
                if asset_copy.get("vnet_name") or asset_copy.get("subnet_name") or asset_copy.get("subnet_id"):
                    asset_copy["network"] = {
                        "vnet": _first_value((asset_copy.get("network") or {}).get("vnet") if isinstance(asset_copy.get("network"), dict) else None, asset_copy.get("vnet_name"), asset_copy.get("parent_vnet_name")),
                        "subnet": _first_value((asset_copy.get("network") or {}).get("subnet") if isinstance(asset_copy.get("network"), dict) else None, asset_copy.get("subnet_name")),
                        "vnet_resource_group": _first_value((asset_copy.get("network") or {}).get("vnet_resource_group") if isinstance(asset_copy.get("network"), dict) else None, asset_copy.get("vnet_resource_group"), asset_copy.get("parent_vnet_resource_group")),
                        "subnet_id": _first_value((asset_copy.get("network") or {}).get("subnet_id") if isinstance(asset_copy.get("network"), dict) else None, asset_copy.get("subnet_id")),
                    }
        elif "fqdns" not in asset_copy:
            resolved_fqdn = subscription_primary_fqdn(asset_copy)
            if resolved_fqdn:
                asset_copy["fqdns"] = [resolved_fqdn]

        visible_assets.append(asset_copy)

    return visible_assets


def subscription_primary_fqdn(asset: dict) -> str:
    fqdns = asset.get("fqdns") or []
    if fqdns:
        return str(fqdns[0]).strip()
    fqdn = str(asset.get("fqdn") or "").strip()
    if fqdn:
        return fqdn
    suffix = subscription_known_fqdn_suffix(asset.get("arm_type") or asset.get("type") or "")
    if suffix:
        name = str(asset.get("name") or "").strip()
        if name:
            return f"{name}.{suffix}"
    return ""


def _aks_ingress_is_public(exposure_level: object, fqdn: object, harvest_is_public: object | None = None) -> bool:
    """Return True only when an AKS ingress should be treated as Internet-facing."""
    if harvest_is_public is False:
        return False

    host = str(fqdn or "").strip().lower()
    if host and (
        ".internal." in host
        or ".privatelink." in host
        or host.endswith(".local")
        or host.endswith(".cluster.local")
        or host.endswith(".svc")
        or host.endswith(".appserviceenvironment.net")
    ):
        return False

    return str(exposure_level or "").strip().lower() == "public"


def subscription_is_secret_store(arm_type: str) -> bool:
    type_key = (arm_type or "").lower()
    return "keyvault" in type_key or "appconfiguration" in type_key


def subscription_data_attack_label(asset: dict) -> str:
    type_key = (asset.get("arm_type") or "").lower()
    if "keyvault" in type_key:
        return "steal secrets"
    if "appconfiguration" in type_key:
        return "read config"
    if "storage" in type_key:
        return "read blobs"
    if "sql" in type_key or "documentdb" in type_key or "search/search" in type_key:
        return "query data"
    if "servicebus" in type_key or "eventhub" in type_key:
        return "abuse messages"
    if "cache/redis" in type_key:
        return "dump cache"
    return "access data"


def subscription_allowlist_label(asset: dict) -> str:
    family = str(asset.get("type") or asset.get("label") or asset.get("friendly_type") or "").strip()
    return f"IP allowlist ({family})" if family else "IP allowlist"


def subscription_is_allowlist_target(asset: dict) -> bool:
    type_key = (asset.get("arm_type") or asset.get("type") or "").lower()
    return any(
        token in type_key
        for token in (
            "apimanagement",
            "sites",
            "managedcluster",
            "containerinstance",
            "datafactory",
            "cognitiveservices",
            "containerregistry",
            "servicefabric",
            "keyvault",
            "storage",
            "sql",
            "documentdb",
            "servicebus",
            "eventhub",
            "cache/redis",
            "search/search",
            "appconfiguration",
            "machinelearningservices",
        )
    )


def subscription_attack_badges(asset: dict) -> list[str]:
    badges: list[str] = []
    if asset.get("public"):
        badges.append("public")
    if asset.get("has_waf") or asset.get("waf_mode"):
        badges.append("waf")
    if asset.get("tier") == "backend":
        badges.append("exec")
    elif subscription_is_secret_store(asset.get("arm_type") or ""):
        badges.append("secrets")
    elif asset.get("tier") == "data":
        badges.append("data")
    elif asset.get("tier") == "api":
        badges.append("auth")
    return badges[:2]


def subscription_asset_label(asset: dict, include_badges: bool = False, include_fqdn: bool = False) -> str:
    parts = [
        asset.get("friendly_type") or asset.get("arm_type") or "resource",
        asset.get("short_name") or asset.get("name") or "resource",
    ]
    if asset.get("node_variant") == "aks_ingress":
        host = str(asset.get("ingress_host") or asset.get("host") or "").strip()
        path = str(asset.get("path") or "").strip()
        if host:
            parts.append(host if len(host) <= 42 else host[:40] + "...")
        if path and path not in ("/*", "*"):
            parts.append(path)
    type_key = (asset.get("arm_type") or "").lower()
    if asset.get("parent_vnet_name") and any(token in type_key for token in ("subnet", "hostingenvironment", "serverfarms", "servicefabric")):
        parts.append(f"vnet: {asset.get('parent_vnet_name')}")
        if asset.get("address_prefix"):
            parts.append(str(asset.get("address_prefix")))
        if asset.get("network_security_group_name"):
            parts.append(f"nsg: {asset.get('network_security_group_name')}")
        if asset.get("route_table_name"):
            parts.append(f"rt: {asset.get('route_table_name')}")
        delegations = asset.get("delegations") or []
        if delegations:
            parts.append(f"delegated: {', '.join(str(d) for d in delegations[:2])}")
    fqdn = subscription_primary_fqdn(asset)
    if include_fqdn and fqdn and asset.get("node_variant") != "aks_ingress":
        parts.append(fqdn if len(fqdn) <= 42 else fqdn[:40] + "...")
    public_ip = str(asset.get("public_ip") or "").strip()
    if public_ip and "publicipaddress" in (asset.get("arm_type") or "").lower():
        parts.append(public_ip if len(public_ip) <= 42 else public_ip[:40] + "...")
    hosted_site_count = asset.get("hosted_site_count")
    if hosted_site_count:
        parts.append(f"{hosted_site_count} app{'s' if hosted_site_count != 1 else ''}")
    badges = subscription_attack_badges(asset) if include_badges else []
    if badges:
        parts.append(" - ".join(badges))
    return "<br/>".join(p for p in parts if p)


def subscription_html_node(node_id: str, label: str, arm_type: str | None, get_icon_path: Callable[[str], str | None]) -> str:
    # Simplified node labels to avoid Mermaid 500-char line limit
    if arm_type:
        icon_path = get_icon_path(arm_type)
        if icon_path:
            safe_label = label.replace("'", "&#39;").replace('"', "&quot;")
            # Compact HTML - use CSS classes instead of inline styles
            html = f"<div class='nd'><img src='{icon_path}' class='ni'/><div class='nl'>{safe_label}</div></div>"
            return f'    {node_id}["{html}"]'
    safe_label = label.replace('"', "&quot;")
    return f'    {node_id}["{safe_label}"]'


def subscription_node_class(asset: dict) -> str:
    tier = asset.get("tier")
    if tier == "entry":
        return "entryPointProtected" if (asset.get("has_waf") or asset.get("waf_mode") or asset.get("is_restricted")) else "entryPoint"
    if tier == "api":
        return "apiGateway"
    if tier == "network":
        return "network"
    if subscription_is_secret_store(asset.get("arm_type") or ""):
        return "secretStore"
    if tier == "data":
        return "dataStorePublic" if asset.get("public") else "dataStore"
    if asset.get("public"):
        return "publicBackend"
    if tier == "backend":
        return "backend"
    return "neutral"


def subscription_register_node(
    node_map: dict,
    asset: dict,
    sanitise_node_id: Callable[[str], str],
) -> None:
    arm_type = (asset.get("arm_type") or "").lower()
    resources = asset.get("resources")
    if resources is None:
        resources = [{"rg": asset.get("rg"), "name": asset.get("name")}]
    node_map[subscription_node_id(asset, sanitise_node_id)] = {
        "title": asset.get("name") or asset.get("friendly_type") or "resource",
        "arm_type": asset.get("arm_type"),
        "resources": resources,
        "can_drill": bool(resources),
    }


def subscription_join_names(items: list[dict], limit: int = 2) -> str:
    names = [str(item.get("name") or item.get("friendly_type") or "resource").strip() for item in items if item]
    if not names:
        return "resource"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" +{len(names) - limit}"


def render_subscription_view(
    *,
    nodes: list[dict],
    edges: list[dict],
    get_icon_path: Callable[[str], str | None],
    node_map: dict | None = None,
    direction: str = "LR",
    title: str = "",
    description: str = "",
    legend: list[str] | None = None,
    attack_paths: list[dict] | None = None,
    asset_summary: dict | None = None,
) -> dict:
    if not nodes:
        nodes = [{"id": "NoData", "label": "No resources found", "class_name": "summary"}]

    lines = [f"graph {direction}"]
    for node in nodes:
        lines.append(subscription_html_node(node["id"], node.get("label") or node["id"], node.get("arm_type"), get_icon_path))

    lines.append("")
    for edge in edges:
        label = str(edge.get("label") or "").replace('"', "&quot;")
        arrow = edge.get("arrow") or "-->"
        if label:
            lines.append(f'    {edge["src"]} {arrow}|"{label}"| {edge["dst"]}')
        else:
            lines.append(f'    {edge["src"]} {arrow} {edge["dst"]}')

    lines.append("")
    for idx, edge in enumerate(edges):
        style = [f'stroke:{edge.get("color", "#ffffff")}', f'stroke-width:{edge.get("width", "2px")}']
        dash = edge.get("dasharray")
        if dash:
            style.append(f"stroke-dasharray:{dash}")
        lines.append(f'    linkStyle {idx} ' + ",".join(style))

    lines.append("")
    lines.append("    classDef internet stroke:#d32f2f,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef entryPoint stroke:#d32f2f,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef entryPointProtected stroke:#ea580c,stroke-width:2px,fill:#3d1c0d;")
    lines.append("    classDef apiGateway stroke:#0ea5e9,stroke-width:2px,fill:#082f49;")
    lines.append("    classDef backend stroke:#22c55e,stroke-width:2px,fill:#052e16;")
    lines.append("    classDef publicBackend stroke:#ef4444,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef dataStore stroke:#2563eb,stroke-width:2px,fill:#172554;")
    lines.append("    classDef dataStorePublic stroke:#ef4444,stroke-width:2px,fill:#3b0a0a;")
    lines.append("    classDef secretStore stroke:#8b5cf6,stroke-width:2px,fill:#2e1065;")
    lines.append("    classDef network stroke:#64748b,stroke-width:2px,fill:#0f172a;")
    lines.append("    classDef summary stroke:#6b7280,stroke-width:2px,fill:#111827;")
    lines.append("    classDef neutral stroke:#6b7280,stroke-width:2px,fill:#111827;")

    for node in nodes:
        class_name = node.get("class_name")
        if class_name:
            lines.append(f'    class {node["id"]} {class_name};')

    css_lines = [
        "/* Subscription Diagram Styling */",
        ".internet { stroke: #d32f2f; stroke-width: 2px; fill: #3b0a0a; }",
        ".entryPoint { stroke: #d32f2f; stroke-width: 2px; fill: #3b0a0a; }",
        ".entryPointProtected { stroke: #ea580c; stroke-width: 2px; fill: #3d1c0d; }",
        ".apiGateway { stroke: #0ea5e9; stroke-width: 2px; fill: #082f49; }",
        ".backend { stroke: #22c55e; stroke-width: 2px; fill: #052e16; }",
        ".publicBackend { stroke: #ef4444; stroke-width: 2px; fill: #3b0a0a; }",
        ".dataStore { stroke: #2563eb; stroke-width: 2px; fill: #172554; }",
        ".dataStorePublic { stroke: #ef4444; stroke-width: 2px; fill: #3b0a0a; }",
        ".secretStore { stroke: #8b5cf6; stroke-width: 2px; fill: #2e1065; }",
        ".network { stroke: #64748b; stroke-width: 2px; fill: #0f172a; }",
        ".summary { stroke: #6b7280; stroke-width: 2px; fill: #111827; }",
    ]

    return {
        "mermaid": "\n".join(lines),
        "css_code": "\n".join(css_lines),
        "icon_map": {},
        "node_drilldown_map": node_map or {},
        "title": title,
        "description": description,
        "legend": legend or [],
        "attack_paths": attack_paths or [],
        "asset_summary": asset_summary or {},
    }


def build_subscription_attack_paths(
    assets: list[dict],
    scope_label: str,
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
) -> list[dict]:
    assets = [
        asset for asset in assets
        if not subscription_should_hide_in_subscription_diagram(asset)
    ]
    entries = [a for a in assets if a.get("tier") == "entry"]
    apis = [a for a in assets if a.get("tier") == "api"]
    backends = [a for a in assets if a.get("tier") == "backend"]
    data = [a for a in assets if a.get("tier") == "data"]
    public_assets = [a for a in assets if a.get("public")]
    secret_stores = [a for a in data if subscription_is_secret_store(a.get("arm_type") or "")]
    public_data = [a for a in data if a.get("public")]

    raw_paths: list[dict] = []

    if entries and (apis or backends):
        chain = ["Internet", subscription_join_names(entries, 1)]
        if apis:
            chain.append(subscription_join_names(apis, 1))
        if backends:
            chain.append(subscription_join_names(backends, 1))
        raw_paths.append(
            {
                "title": f"Public ingress into {scope_label}",
                "path": " -> ".join(chain),
                "summary": "A public edge service can expose a reachable path into application workloads.",
                "impact": f"Initial foothold in {scope_label} could turn edge exposure into backend compromise.",
                "confidence": "medium" if any(e.get("has_waf") or e.get("waf_mode") or e.get("is_restricted") for e in entries) else "high",
                "source": "subscription-diagram",
                "evidence": [f"Public entry points: {subscription_join_names(entries)}"]
                + ([f"API tier: {subscription_join_names(apis)}"] if apis else []),
            }
        )

    public_backends = [a for a in backends if a.get("public")]
    if public_backends:
        raw_paths.append(
            {
                "title": f"Direct workload exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(public_backends, 2)}",
                "summary": "Public compute removes a gateway hop and gives attackers a direct path to code execution surfaces.",
                "impact": "A direct exploit can bypass upstream controls and land in application runtime.",
                "confidence": "high",
                "source": "subscription-diagram",
                "evidence": [f"Public backend workloads: {subscription_join_names(public_backends)}"],
            }
        )

    if not raw_paths and entries:
        raw_paths.append(
            {
                "title": f"Public edge exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(entries, 2)}",
                "summary": "The subscription exposes an internet-facing edge even when downstream routing details are not yet harvested.",
                "impact": "Treat the edge tier as the first attacker foothold and validate what workloads sit behind it.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Public entry points: {subscription_join_names(entries)}"],
            }
        )

    if backends and secret_stores:
        raw_paths.append(
            {
                "title": f"Secrets pivot from compute in {scope_label}",
                "path": f"{subscription_join_names(backends, 1)} -> {subscription_join_names(secret_stores, 2)}",
                "summary": "Compromised compute often pivots by reading secrets, connection strings, or app configuration.",
                "impact": "Secret theft can expand blast radius into databases, storage, or downstream APIs.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Secret-bearing services: {subscription_join_names(secret_stores)}"],
            }
        )

    non_secret_data = [a for a in data if a not in secret_stores]
    if backends and non_secret_data:
        raw_paths.append(
            {
                "title": f"Data access after workload compromise in {scope_label}",
                "path": f"{subscription_join_names(backends, 1)} -> {subscription_join_names(non_secret_data, 2)}",
                "summary": "Once attackers land on compute, data-plane resources become the obvious next objective.",
                "impact": "Could lead to data theft, tampering, or service disruption.",
                "confidence": "medium",
                "source": "subscription-diagram",
                "evidence": [f"Data services in scope: {subscription_join_names(non_secret_data)}"],
            }
        )

    if public_data:
        raw_paths.append(
            {
                "title": f"Direct public data exposure in {scope_label}",
                "path": f"Internet -> {subscription_join_names(public_data, 2)}",
                "summary": "Internet-reachable data services create direct attack paths without an application compromise step.",
                "impact": "Exposure may allow direct data access, enumeration, or brute-force attempts.",
                "confidence": "high",
                "source": "subscription-diagram",
                "evidence": [f"Public data services: {subscription_join_names(public_data)}"],
            }
        )

    if not raw_paths and not public_assets:
        raw_paths.append(
            {
                "title": f"No direct public path identified in {scope_label}",
                "path": "Private-only or internal-facing topology",
                "summary": "This view did not find an obvious internet-origin attack path from harvested exposure flags.",
                "impact": "Focus next on identity, CI/CD, and control-plane pivots rather than direct ingress.",
                "confidence": "low",
                "source": "subscription-diagram",
                "evidence": ["No harvested public assets in scope"],
            }
        )

    return normalize_attack_paths(raw_paths, reviewer="subscription-diagram")


def build_subscription_overlay_views(
    rows: list,
    *,
    sanitise_node_id: Callable[[str], str],
    friendly_type: Callable[[str], str],
    get_icon_path: Callable[[str], str | None],
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
    plan_links: list | None = None,
) -> dict:
    assets = subscription_assets_from_rows(rows, friendly_type)
    assets = subscription_apply_plan_hierarchy(assets, plan_links)
    assets = [
        asset for asset in assets
        if not subscription_should_hide_in_subscription_diagram(asset)
    ]
    entries = [a for a in assets if a.get("tier") == "entry"]
    apis = [a for a in assets if a.get("tier") == "api"]
    backends = [a for a in assets if a.get("tier") == "backend"]
    data = [a for a in assets if a.get("tier") == "data"]
    public_assets = [a for a in assets if a.get("public")]
    attack_paths = build_subscription_attack_paths(assets, "this subscription", normalize_attack_paths)
    asset_summary = {
        "entry_points": len(entries),
        "api_layer": len(apis),
        "backends": len(backends),
        "data_stores": len(data),
        "public_assets": len(public_assets),
    }

    return {
        "attack_paths_summary": attack_paths,
        "asset_summary": asset_summary,
    }


def build_subscription_diagrams_by_rg(
    sub_name: str,
    environment: str,
    rows: list,
    *,
    sanitise_node_id: Callable[[str], str],
    friendly_type: Callable[[str], str],
    get_icon_path: Callable[[str], str | None],
    normalize_attack_paths: Callable[[object, str | None], list[dict]],
    plan_links: list | None = None,
    aks_route_rows: list | None = None,
) -> list[dict]:
    del sub_name, environment
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for asset in subscription_assets_from_rows(rows, friendly_type):
        groups[asset.get("rg") or "default"].append(asset)

    if aks_route_rows:
        seen_route_keys: set[str] = set()

        for row in aks_route_rows:
            try:
                if len(row) >= 12:
                    cluster_name, namespace, ingress_name, host, path, exposure_level, service_name, service_port, deployment_name, git_repository, resource_group, pod_template_labels = row[:12]
                else:
                    cluster_name, namespace, ingress_name, host, path, service_name, service_port, deployment_name, git_repository, resource_group, pod_template_labels = row[:11]
                    exposure_level = None
            except Exception:
                continue

            cluster_rg = str(resource_group or "").strip()
            cluster_key = (cluster_rg.lower(), str(cluster_name or "").strip().lower())
            if not cluster_key[1]:
                continue

            ingress_label = str(ingress_name or host or f"{cluster_name}-ingress").strip()
            route_key = "::".join([
                cluster_key[0],
                cluster_key[1],
                str(namespace or "").strip().lower(),
                ingress_label.lower(),
                str(host or "").strip().lower(),
                str(path or "").strip().lower(),
                str(service_name or deployment_name or "").strip().lower(),
                str(service_port or "").strip().lower(),
            ])
            if route_key in seen_route_keys:
                continue
            seen_route_keys.add(route_key)

            # Skip synthetic AKS service nodes unless the route row actually
            # carries ingress context. This keeps the diagram focused on
            # ingress-exposed services instead of every cluster-side service.
            if not any(str(value or "").strip() for value in (ingress_name, host, path)):
                continue

            ingress_display = str(host or "").strip() or ingress_label
            ingress_asset = {
                "name": f"{cluster_name}-{namespace}-{ingress_label}-{str(host or path or 'route').replace('/', '_')}-ingress",
                "node_variant": "aks_ingress",
                "arm_type": "kubernetes_ingress",
                "rg": cluster_rg or "default",
                "fqdn": str(host or "").strip(),
                "host": str(host or "").strip(),
                "ingress_host": str(host or "").strip(),
                "path": str(path or "").strip(),
                "public": _aks_ingress_is_public(exposure_level, host),
                "sku": None,
                "id": f"aks-ingress::{route_key}",
                "has_waf": False,
                "waf_mode": None,
                "listeners": None,
                "routing_targets": [{"target": cluster_name, "name": cluster_name, "type": "Kubernetes Cluster"}],
                "is_restricted": False,
                "tier": "api",
                "friendly_type": friendly_type("kubernetes_ingress"),
                "short_name": ingress_display,
                "label": ingress_display,
                "auth_methods": [],
                "resources": [],
                "source_cluster_rg": cluster_rg,
                "source_cluster_name": cluster_name,
                "source_namespace": namespace,
                "exposure_level": exposure_level,
                "source_service": service_name,
                "source_deployment": deployment_name,
                "source_repo": git_repository,
                "source_labels": pod_template_labels,
            }

            groups[cluster_rg or "default"].append(ingress_asset)

    diagrams = []

    def rg_asset_summary(rg_assets: list[dict]) -> dict:
        hosted_site_keys = {
            ((site_name or "").lower(), (site_rg or "").lower())
            for site_rg, site_name, _plan_rg, _plan_name in (plan_links or [])
        }
        def _is_visible_backend(asset: dict) -> bool:
            if asset.get("tier") != "backend":
                return False
            key = ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower())
            if key in hosted_site_keys and "sites" in (asset.get("arm_type") or "").lower():
                return False
            return True

        return {
            "entry_points": sum(1 for asset in rg_assets if asset.get("tier") == "entry"),
            "api_layer": sum(1 for asset in rg_assets if asset.get("tier") == "api"),
            "backends": sum(1 for asset in rg_assets if _is_visible_backend(asset)),
            "data_stores": sum(1 for asset in rg_assets if asset.get("tier") == "data"),
            "public_assets": sum(1 for asset in rg_assets if asset.get("public")),
        }

    def build_rg_view(rg: str, rg_assets: list[dict]) -> tuple[dict, int]:
        rg_assets = subscription_apply_plan_hierarchy(rg_assets, plan_links)
        diagram_assets = [
            asset for asset in rg_assets
            if not subscription_should_hide_in_subscription_diagram(asset)
        ]
        summary = rg_asset_summary(diagram_assets)
        hosted_site_keys = {
            ((site_name or "").lower(), (site_rg or "").lower())
            for site_rg, site_name, _plan_rg, _plan_name in (plan_links or [])
        }
        visible_rg_assets = [
            asset
            for asset in diagram_assets
            if not (
                asset.get("tier") == "backend"
                and "sites" in (asset.get("arm_type") or "").lower()
                and ((asset.get("name") or "").lower(), (asset.get("rg") or "").lower()) in hosted_site_keys
            )
        ]
        route_nodes_by_cluster: dict[tuple[str, str], list[dict]] = defaultdict(list)
        route_nodes_by_host: dict[str, list[dict]] = defaultdict(list)
        route_nodes_by_name: dict[str, list[dict]] = defaultdict(list)
        visible_nodes_by_name: dict[str, set[str]] = defaultdict(set)
        visible_nodes_by_name_normalized: dict[str, set[str]] = defaultdict(set)
        visible_nodes_by_fqdn: dict[str, set[str]] = defaultdict(set)
        visible_nodes_by_fqdn_normalized: dict[str, set[str]] = defaultdict(set)
        visible_nodes_by_resource_id: dict[str, str] = {}

        def _routing_lookup_key(value: str) -> str:
            import re as _re

            text = str(value or "").strip().lower().rstrip(".")
            if not text:
                return ""
            text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
            text = text.split(":", 1)[0].rstrip(".")
            for prefix in ("production-", "prod-", "subscription-production-"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            text = _re.sub(r"-(uksouth|ukwest|eastus\d*|westeurope|northeurope|westus\d*)$", "", text, flags=_re.IGNORECASE)
            env_suffix = _re.compile(r"-(prod|production|dev|stg|staging|uat|int|internal|ext|external|api|svc|service)$", _re.IGNORECASE)
            while True:
                next_text = env_suffix.sub("", text)
                if next_text == text:
                    break
                text = next_text
            text = _re.sub(r"-(f|fn|func|function)$", "", text, flags=_re.IGNORECASE)
            return _re.sub(r"[^a-z0-9]+", "", text)

        for asset in visible_rg_assets:
            node_id = subscription_node_id(asset, sanitise_node_id)
            asset_id = str(asset.get("id") or "").strip().lower()
            if asset_id:
                visible_nodes_by_resource_id[asset_id] = node_id
            for candidate in (
                asset.get("name"),
                asset.get("short_name"),
                asset.get("ingress_name"),
                asset.get("source_service"),
                asset.get("source_deployment"),
            ):
                candidate_key = str(candidate or "").strip().lower()
                if candidate_key:
                    visible_nodes_by_name[candidate_key].add(node_id)
                    normalized = _routing_lookup_key(candidate_key)
                    if normalized:
                        visible_nodes_by_name_normalized[normalized].add(node_id)
            for candidate in (
                asset.get("fqdn"),
                asset.get("host"),
                asset.get("ingress_host"),
            ):
                candidate_key = str(candidate or "").strip().lower()
                if candidate_key:
                    visible_nodes_by_fqdn[candidate_key].add(node_id)
                    normalized = _routing_lookup_key(candidate_key)
                    if normalized:
                        visible_nodes_by_fqdn_normalized[normalized].add(node_id)
            variant = asset.get("node_variant")
            if variant != "aks_ingress":
                continue
            cluster_key = (
                str(asset.get("source_cluster_rg") or asset.get("rg") or "").strip().lower(),
                str(asset.get("source_cluster_name") or "").strip().lower(),
            )
            if cluster_key[1]:
                route_nodes_by_cluster[cluster_key].append(asset)
            if variant == "aks_ingress":
                for candidate in (
                    asset.get("host"),
                    asset.get("ingress_host"),
                    asset.get("fqdn"),
                ):
                    host_key = str(candidate or "").strip().lower()
                    if host_key:
                        route_nodes_by_host[host_key].append(asset)
                name_key = str(asset.get("ingress_name") or asset.get("short_name") or asset.get("name") or "").strip().lower()
                if name_key:
                    route_nodes_by_name[name_key].append(asset)

        entries = [a for a in visible_rg_assets if a.get("tier") == "entry"]
        apis = [a for a in visible_rg_assets if a.get("tier") == "api"]
        backends = [a for a in visible_rg_assets if a.get("tier") == "backend"]
        routing_backends = [
            a for a in visible_rg_assets
            if a.get("tier") == "backend" or str(a.get("arm_type") or "").strip().lower() == "apim backend target"
        ]
        data = [a for a in visible_rg_assets if a.get("tier") == "data"]
        public_assets = [a for a in visible_rg_assets if a.get("public")]

        # Detect AKS/SF-only RGs: compute clusters with no entry points or API layer
        # in scope. These clusters are connected at the subscription level (via APIM)
        # but appear orphaned in the per-RG view without a context hint.
        _cluster_arm_types = ("managedclusters", "servicefabric/clusters")
        _is_cluster_only_rg = (
            not entries
            and not apis
            and any(
                any(t in (a.get("arm_type") or "").lower() for t in _cluster_arm_types)
                for a in backends
            )
        )

        nodes: list[dict] = []
        node_map: dict = {}
        seen_nodes: set[str] = set()
        if public_assets or entries:
            nodes.append({"id": "Internet", "label": "Internet", "class_name": "internet"})
            seen_nodes.add("Internet")

        # For cluster-only RGs add a synthetic context node so the user knows
        # the cluster is reachable via the subscription-level APIM, not truly orphaned.
        _sub_ctx_node_id: str | None = None
        if _is_cluster_only_rg:
            _sub_ctx_node_id = "sub_level_apim_context"
            nodes.append({
                "id": _sub_ctx_node_id,
                "label": "🔗 APIM<br/>(subscription level)",
                "arm_type": "microsoft.apimanagement/service",
                "class_name": "apiGateway",
            })
            seen_nodes.add(_sub_ctx_node_id)

        def add_asset_node(asset: dict, *, badges: bool = False, include_fqdn: bool = False) -> None:
            node_id = subscription_node_id(asset, sanitise_node_id)
            if node_id in seen_nodes:
                return
            seen_nodes.add(node_id)
            # Route-bearing resources usually show their DNS target, but AKS ingress
            # nodes should stay name-only because the ingress name is the stable
            # identifier users expect in the diagram.
            show_fqdn = (
                (include_fqdn or asset.get("tier") == "api" or bool(asset.get("routing_targets")))
                and asset.get("node_variant") != "aks_ingress"
            )
            nodes.append(
                {
                    "id": node_id,
                    "label": subscription_asset_label(asset, include_badges=badges, include_fqdn=show_fqdn),
                    "arm_type": asset.get("arm_type"),
                    "class_name": subscription_node_class(asset),
                }
            )
            subscription_register_node(node_map, asset, sanitise_node_id)

        exposure_entries = entries[:4]
        exposure_apis = apis[:2]
        exposure_public_backends = [a for a in backends if a.get("public")][:4]
        exposure_internal_backends = [a for a in backends if not a.get("public")][:3] if (entries or apis) else []
        exposure_backends = exposure_public_backends + exposure_internal_backends
        exposure_public_data = [a for a in data if a.get("public")][:3]
        exposure_internal_data = [a for a in data if not a.get("public")][:3] if (entries or apis or exposure_public_backends) else []
        exposure_data = exposure_public_data + exposure_internal_data

        for asset in visible_rg_assets:
            add_asset_node(asset, badges=False, include_fqdn=False)

        edges: list[dict] = []
        edge_keys: set[tuple[str, str, str]] = set()
        asset_by_id = {
            str(asset.get("id")).lower(): asset
            for asset in visible_rg_assets
            if asset.get("id")
        }

        def add_edge(src: str, dst: str, label: str, color: str, *, width: str = "2px", dasharray: str | None = None) -> None:
            if src not in seen_nodes or dst not in seen_nodes:
                return
            if src == dst:
                return
            key = (src, dst, label)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edge: dict = {"src": src, "dst": dst, "label": label, "color": color, "width": width}
            if dasharray:
                edge["dasharray"] = dasharray
            edges.append(edge)

        def _entry_edge_style(asset: dict) -> tuple[str, str]:
            protected = bool(asset.get("has_waf") or asset.get("waf_mode"))
            restricted = bool(asset.get("is_restricted"))
            waf_mode = (asset.get("waf_mode") or "").strip().lower()
            if protected and restricted:
                return "IP allowlist", "#f97316"
            if protected:
                return "", "#f97316"
            if restricted:
                return subscription_allowlist_label(asset), "#f59e0b"
            return subscription_primary_fqdn(asset) or "public edge", "#ef4444"

        # Network topology: VNet -> subnet and subnet -> contained resources.
        subnet_assets = [
            asset for asset in visible_rg_assets
            if asset.get("parent_vnet_name")
            and any(token in (asset.get("arm_type") or "").lower() for token in ("subnet", "hostingenvironment", "serverfarms", "servicefabric"))
        ]
        for subnet in subnet_assets:
            subnet_node_id = subscription_node_id(subnet, sanitise_node_id)
            parent_vnet_id = str(subnet.get("parent_vnet_id") or "").lower()
            parent_vnet_asset = asset_by_id.get(parent_vnet_id)
            if parent_vnet_asset:
                add_edge(
                    subscription_node_id(parent_vnet_asset, sanitise_node_id),
                    subnet_node_id,
                    "contains",
                    "#64748b",
                    dasharray="6,3",
                )

        for asset in visible_rg_assets:
            subnet_id = str(asset.get("subnet_id") or "").lower()
            if not subnet_id:
                continue
            subnet_asset = asset_by_id.get(subnet_id)
            if not subnet_asset:
                continue
            add_edge(
                subscription_node_id(subnet_asset, sanitise_node_id),
                subscription_node_id(asset, sanitise_node_id),
                "in subnet",
                "#64748b",
                dasharray="6,3",
            )

        # For cluster-only RGs, connect each cluster to the synthetic APIM context node
        # so it's clear the cluster is not orphaned — it's reachable from the subscription-level APIM.
        # Route APIM directly to the ingress resource nodes so the ingress name is the
        # visible entry point for the cluster.
        if _sub_ctx_node_id:
            for backend in backends:
                arm_type_low = (backend.get("arm_type") or "").lower()
                if any(t in arm_type_low for t in _cluster_arm_types):
                    cluster_nid = subscription_node_id(backend, sanitise_node_id)
                    if cluster_nid not in seen_nodes:
                        continue
                    _bk = ((backend.get("rg") or "").strip().lower(), (backend.get("name") or "").strip().lower())
                    _ctx_route_nodes = [
                        item for item in route_nodes_by_cluster.get(_bk, [])
                        if (item.get("node_variant") or "") == "aks_ingress"
                    ]
                    if _ctx_route_nodes:
                        for _route_asset in _ctx_route_nodes:
                            route_nid = subscription_node_id(_route_asset, sanitise_node_id)
                            if route_nid in seen_nodes:
                                add_edge(_sub_ctx_node_id, route_nid, "", "#f97316", dasharray="6,3")
                                add_edge(route_nid, cluster_nid, "", "#f97316", dasharray="6,3")
                    else:
                        add_edge(_sub_ctx_node_id, cluster_nid, "", "#f97316", dasharray="6,3")

        def _parse_routing_targets(raw_targets: object) -> list[dict]:
            if not raw_targets:
                return []
            if isinstance(raw_targets, list):
                return [item for item in raw_targets if isinstance(item, dict)]
            if isinstance(raw_targets, str):
                try:
                    parsed = json.loads(raw_targets)
                except Exception:
                    return []
                if isinstance(parsed, list):
                    return [item for item in parsed if isinstance(item, dict)]
            return []

        def _routing_target_candidates(target: dict) -> list[str]:
            candidates: list[str] = []
            for key in (
                "target_resource_id",
                "targetResourceId",
                "resource_id",
                "backend_fqdn",
                "backendFqdn",
                "backend_target",
                "backendTarget",
                "backend_url",
                "backendUrl",
                "hostname",
                "host",
                "fqdn",
                "target",
                "name",
            ):
                value = str(target.get(key) or "").strip()
                if value and value not in candidates:
                    candidates.append(value)
            return candidates

        def _cluster_key(asset: dict) -> tuple[str, str]:
            return ((asset.get("rg") or "").strip().lower(), (asset.get("name") or "").strip().lower())

        cluster_assets = [
            asset
            for asset in routing_backends
            if any(t in (asset.get("arm_type") or "").lower() for t in _cluster_arm_types)
        ]

        for backend in routing_backends:
            if (backend.get("node_variant") or "") in {"aks_ingress", "aks_service"}:
                continue
            targets = _parse_routing_targets(backend.get("routing_targets"))
            if not targets:
                continue
            backend_nid = subscription_node_id(backend, sanitise_node_id)
            if backend_nid not in seen_nodes:
                continue
            matched_route_nids: set[str] = set()
            matched_cluster_keys: set[tuple[str, str]] = set()
            for target in targets:
                target_value = str(target.get("target") or target.get("name") or "").strip().lower()
                target_rid = str(target.get("target_resource_id") or "").strip().lower()
                for candidate in _routing_target_candidates(target):
                    candidate_key = candidate.strip().lower()
                    if candidate_key:
                        for route_asset in route_nodes_by_host.get(candidate_key, []):
                            route_nid = subscription_node_id(route_asset, sanitise_node_id)
                            if route_nid in seen_nodes:
                                matched_route_nids.add(route_nid)
                        for route_asset in route_nodes_by_name.get(candidate_key, []):
                            route_nid = subscription_node_id(route_asset, sanitise_node_id)
                            if route_nid in seen_nodes:
                                matched_route_nids.add(route_nid)
                for cluster_asset in cluster_assets:
                    cluster_key = _cluster_key(cluster_asset)
                    cluster_id = str(cluster_asset.get("id") or "").strip().lower()
                    cluster_name = cluster_key[1]
                    if target_rid and target_rid == cluster_id:
                        matched_cluster_keys.add(cluster_key)
                    elif target_value and target_value in {cluster_name, str(cluster_asset.get("short_name") or "").strip().lower()}:
                        matched_cluster_keys.add(cluster_key)
                if target_rid and target_rid in visible_nodes_by_resource_id:
                    matched_route_nids.add(visible_nodes_by_resource_id[target_rid])
                for candidate in _routing_target_candidates(target):
                    candidate_key = candidate.strip().lower()
                    if not candidate_key:
                        continue
                    matched_route_nids.update(visible_nodes_by_name.get(candidate_key, set()))
                    matched_route_nids.update(visible_nodes_by_fqdn.get(candidate_key, set()))
                    normalized = _routing_lookup_key(candidate_key)
                    if normalized:
                        matched_route_nids.update(visible_nodes_by_name_normalized.get(normalized, set()))
                        matched_route_nids.update(visible_nodes_by_fqdn_normalized.get(normalized, set()))

            for route_nid in matched_route_nids:
                add_edge(backend_nid, route_nid, "", "#f97316", dasharray="6,3")

            for cluster_key in matched_cluster_keys:
                for route_asset in route_nodes_by_cluster.get(cluster_key, []):
                    if (route_asset.get("node_variant") or "") != "aks_ingress":
                        continue
                    route_nid = subscription_node_id(route_asset, sanitise_node_id)
                    if route_nid in seen_nodes:
                        add_edge(backend_nid, route_nid, "", "#f97316", dasharray="6,3")

        for cluster_asset in cluster_assets:
            cluster_key = _cluster_key(cluster_asset)
            cluster_nid = subscription_node_id(cluster_asset, sanitise_node_id)
            if cluster_nid not in seen_nodes:
                continue
            for route_asset in route_nodes_by_cluster.get(cluster_key, []):
                route_nid = subscription_node_id(route_asset, sanitise_node_id)
                if route_nid not in seen_nodes:
                    continue
                if (route_asset.get("node_variant") or "") == "aks_ingress":
                    add_edge(route_nid, cluster_nid, "", "#f97316", dasharray="6,3")

        for asset in public_assets:
            if asset.get("tier") == "entry":
                label, color = _entry_edge_style(asset)
            else:
                has_waf = bool(asset.get("has_waf") or asset.get("waf_mode"))
                waf_mode = (asset.get("waf_mode") or "").strip().lower()
                if has_waf and "prevention" in waf_mode:
                    color = "#f97316"
                elif has_waf and "detection" in waf_mode:
                    color = "#f59e0b"
                elif has_waf:
                    color = "#f97316"
                else:
                    color = "#ef4444"
                label = subscription_primary_fqdn(asset) or "direct public"
            add_edge("Internet", subscription_node_id(asset, sanitise_node_id), label, color, width="3px")

        for asset in [a for a in exposure_apis if a.get("is_restricted") and not a.get("public") and subscription_is_allowlist_target(a)]:
            node_id = subscription_node_id(asset, sanitise_node_id)
            if node_id in seen_nodes:
                add_edge("Internet", node_id, subscription_allowlist_label(asset), "#f59e0b", width="2px")

        for entry in exposure_entries:
            if not entry.get("public"):
                continue
            targets = exposure_apis[:2] or exposure_backends[:3]
            for target in targets:
                add_edge(
                    subscription_node_id(entry, sanitise_node_id),
                    subscription_node_id(target, sanitise_node_id),
                    "routing",
                    "#f97316",
                )

        if exposure_apis and exposure_backends:
            for api in exposure_apis[:2]:
                for backend in exposure_backends[:3]:
                    add_edge(
                        subscription_node_id(api, sanitise_node_id),
                        subscription_node_id(backend, sanitise_node_id),
                        "backend reach",
                        "#ffffff",
                    )

        if exposure_backends and exposure_data:
            for backend in exposure_backends:
                for store in exposure_data:
                    add_edge(
                        subscription_node_id(backend, sanitise_node_id),
                        subscription_node_id(store, sanitise_node_id),
                        "data flow",
                        "#ffffff",
                    )

        descriptions = {
            "connectivity": "Shows inferred application, API, data, and hosting relationships inside this resource group.",
        }
        if _is_cluster_only_rg:
            descriptions["connectivity"] = (
                "This resource group contains a compute cluster (AKS or Service Fabric) with no entry points in scope. "
                "The dashed orange edge shows the cluster is connected at the subscription level via APIM."
            )
        legends = {
            "connectivity": [
                "Orange edges: WAF-protected entry point",
                "Red edges: directly public — no WAF or network restriction",
                "Amber edges: WAF in Detection mode or IP-allowlisted access",
                "White edges: inferred internal application or hosting flow",
            ],
        }
        if _is_cluster_only_rg:
            legends["connectivity"] = [
                "Dashed orange: subscription-level APIM connection (cross-RG)",
            ] + legends["connectivity"]
        attack_paths = build_subscription_attack_paths(rg_assets, f"resource group {rg}", normalize_attack_paths)
        return (
            render_subscription_view(
                nodes=nodes,
                edges=edges,
                get_icon_path=get_icon_path,
                node_map=node_map,
                direction="TD",
                title=f"{rg} - Connectivity",
                description=descriptions["connectivity"],
                legend=legends["connectivity"],
                attack_paths=attack_paths,
                asset_summary=summary,
            ),
            len(edges),
        )

    for rg in sorted(groups.keys()):
        rg_assets = groups[rg]
        connectivity_view, relationship_count = build_rg_view(rg, rg_assets)
        diagrams.append(
            {
                "rg": rg,
                "mermaid": connectivity_view["mermaid"],
                "css_code": connectivity_view["css_code"],
                "icon_map": {},
                "node_drilldown_map": connectivity_view["node_drilldown_map"],
                "asset_count": len(rg_assets),
                "public_count": sum(1 for asset in rg_assets if asset.get("public")),
                "relationship_count": relationship_count,
                "asset_summary": connectivity_view["asset_summary"],
                "attack_paths": connectivity_view.get("attack_paths", []),
                "default_view": "connectivity",
                "views": {
                    "connectivity": connectivity_view,
                },
            }
        )

    return diagrams
