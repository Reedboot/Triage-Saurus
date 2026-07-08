#!/usr/bin/env python3
"""Unit tests for Scripts/Harvest/Azure helpers and correlate_assets logic.

These tests use only pure Python (no az CLI, no DB) by exercising functions
that are isolated from external dependencies.
"""
import json
import ipaddress
import re
import sys
import sqlite3
import threading
from concurrent.futures import Future
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Harvest"))
sys.path.insert(0, str(ROOT / "Scripts" / "Harvest" / "Azure"))
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

import pytest
import harvest_azure_assets
import apim_routing_map
import appgw_routing_map
import correlate_assets
import db_helpers
from Azure._helpers import (
    safe_str,
    infer_fqdn,
    build_endpoints,
    extract_ip_restrictions,
    infer_sku,
    normalize_route_path,
    route_path_matches,
)
from Azure._staged import BackfillJob, StagedRows
from Azure import app_configuration, storage, aks, key_vault, sql_server, service_bus, event_hub, virtual_network
from Azure import load_balancer
from Azure import virtual_machine
from Azure import machine_learning


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


# ---------------------------------------------------------------------------
# safe_str
# ---------------------------------------------------------------------------

class TestSafeStr:
    def test_returns_string_for_plain_value(self):
        assert safe_str("hello") == "hello"

    def test_strips_whitespace(self):
        assert safe_str("  spaces  ") == "spaces"

    def test_returns_none_for_none(self):
        assert safe_str(None) is None

    def test_returns_none_for_empty_string(self):
        assert safe_str("") is None

    def test_returns_none_for_whitespace_only(self):
        assert safe_str("   ") is None

    def test_coerces_int_to_string(self):
        assert safe_str(42) == "42"


class TestLoadBalancerHarvest:
    def test_extract_public_ip_ids_from_frontend_configs(self):
        resource = {
            "properties": {
                "frontendIPConfigurations": [
                    {"properties": {"publicIPAddress": {"id": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one"}}},
                    {"properties": {"publicIPAddressId": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-two"}},
                ]
            }
        }
        ids = load_balancer._extract_public_ip_ids(resource)
        assert sorted(ids) == sorted([
            "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one",
            "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-two",
        ])
        assert load_balancer._is_public(resource) is True

    def test_harvest_uses_lb_show_details_for_public_detection(self, monkeypatch):
        lb_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/loadBalancers/lb-one"
        pip_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/publicIPAddresses/pip-one"

        list_row = {
            "id": lb_id,
            "name": "lb-one",
            "resourceGroup": "rg-net",
            "type": "Microsoft.Network/loadBalancers",
            "location": "ukwest",
            "properties": {
                # Simulate shallow `az resource list` payload with no frontend details.
                "frontendIPConfigurations": []
            },
        }
        detailed_row = {
            **list_row,
            "properties": {
                "frontendIPConfigurations": [
                    {"properties": {"publicIPAddress": {"id": pip_id}}}
                ],
                "backendAddressPools": [
                    {
                        "name": "pool-1",
                        "backendIPConfigurations": [
                            {
                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Compute/virtualMachineScaleSets/power_bi_gateway/virtualMachines/0/networkInterfaces/backend/ipConfigurations/public"
                            }
                        ],
                    }
                ],
                "probes": [{"name": "probe-1"}],
            },
        }

        def fake_az(args, subscription_id):
            if args[:3] == ["resource", "list", "--resource-type"]:
                return [list_row]
            if args[:3] == ["network", "lb", "show"]:
                return detailed_row
            return []

        monkeypatch.setattr(load_balancer, "az", fake_az)

        rows = load_balancer.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == lb_id
        assert row["is_public"] == 1
        raw = json.loads(row["raw_json"])
        assert raw["properties"]["frontendIPConfigurations"][0]["properties"]["publicIPAddress"]["id"] == pip_id
        assert raw["_extra"]["public_ip_resource_ids"] == [pip_id]
        assert raw["_extra"]["routing_targets"][0]["target"] == "power_bi_gateway"


class TestMachineLearningHarvest:
    def test_harvest_extracts_workspace_url_and_public_state(self, monkeypatch):
        workspace_id = "/subscriptions/sub-1/resourceGroups/rg-ai/providers/Microsoft.MachineLearningServices/workspaces/ml-prod"
        workspace = {
            "id": workspace_id,
            "name": "ml-prod",
            "resourceGroup": "rg-ai",
            "type": "Microsoft.MachineLearningServices/workspaces",
            "location": "uksouth",
            "properties": {
                "workspaceUrl": "https://ml-prod.uksouth.api.azureml.ms",
                "publicNetworkAccess": "Enabled",
                "privateEndpointConnections": [],
            },
            "tags": {"pipeline": "customer"},
        }

        def fake_az(args, subscription_id):
            assert args == ["resource", "list", "--resource-type", machine_learning.RESOURCE_TYPE]
            assert subscription_id == "sub-1"
            return [workspace]

        monkeypatch.setattr(machine_learning, "az", fake_az)

        rows = machine_learning.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == workspace_id
        assert row["fqdn"] == "ml-prod.uksouth.api.azureml.ms"
        assert row["is_public"] == 1
        assert row["is_restricted"] == 0
        raw = json.loads(row["raw_json"])
        assert raw["_extra"]["workspace_url"] == "https://ml-prod.uksouth.api.azureml.ms"


# ---------------------------------------------------------------------------
# infer_fqdn
# ---------------------------------------------------------------------------

class TestInferFqdn:
    def test_returns_default_hostname(self):
        resource = {"properties": {"defaultHostName": "myapp.azurewebsites.net"}}
        assert infer_fqdn(resource) == "myapp.azurewebsites.net"

    def test_returns_none_for_empty_resource(self):
        assert infer_fqdn({}) is None

    def test_returns_none_for_no_properties(self):
        assert infer_fqdn({"id": "/subscriptions/123"}) is None

    def test_returns_first_host_from_list(self):
        resource = {"properties": {"hostNames": ["app.example.com", "app2.example.com"]}}
        result = infer_fqdn(resource)
        assert result == "app.example.com"

    def test_skips_empty_list(self):
        resource = {"properties": {"hostNames": []}}
        assert infer_fqdn(resource) is None


# ---------------------------------------------------------------------------
# infer_sku
# ---------------------------------------------------------------------------

class TestInferSku:
    def test_returns_sku_name_from_standard_location(self):
        resource = {"sku": {"name": "Standard_v2", "tier": "Standard"}}
        result = infer_sku(resource)
        assert "Standard_v2" in result or result == "Standard_v2"

    def test_returns_none_for_missing_sku(self):
        assert infer_sku({}) is None

    def test_handles_sku_with_only_name(self):
        resource = {"sku": {"name": "B1"}}
        result = infer_sku(resource)
        assert result is not None


# ---------------------------------------------------------------------------
# build_endpoints
# ---------------------------------------------------------------------------

class TestBuildEndpoints:
    def test_empty_list_returns_empty_json_array(self):
        import json
        result = build_endpoints([])
        parsed = json.loads(result)
        assert parsed == []

    def test_returns_json_string(self):
        import json
        # build_endpoints takes (address, port, protocol) tuples
        entries = [("myapp.azurewebsites.net", 443, "https")]
        result = build_endpoints(entries)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["address"] == "myapp.azurewebsites.net"

    def test_multiple_probes_run_concurrently(self, monkeypatch):
        import threading

        barrier = threading.Barrier(4, timeout=2)

        def fake_probe(address, port, protocol, timeout=5):
            barrier.wait()
            return {
                "reachable": True,
                "probe_latency_ms": 1,
                "probe_error": None,
                "probe_note": "tcp_ok",
            }

        monkeypatch.setattr("Azure._helpers._probe_endpoint", fake_probe)
        entries = [(f"host{i}.example.com", 443, "https") for i in range(4)]
        result = build_endpoints(entries)
        parsed = json.loads(result)
        assert [item["address"] for item in parsed] == [f"host{i}.example.com" for i in range(4)]


class TestRoutePathHelpers:
    def test_normalize_route_path_adds_leading_slash_and_trims_trailing(self):
        assert normalize_route_path("api/v1/") == "/api/v1"

    def test_route_path_matches_prefix_wildcard(self):
        assert route_path_matches("/api/*", "/api/orders/123")

    def test_route_path_matches_template_variables(self):
        assert route_path_matches("/api/{id}", "/api/orders")

    def test_route_path_matches_rejects_different_roots(self):
        assert route_path_matches("/api/*", "/admin") is False


class TestStorageHarvest:
    def test_accounts_run_concurrently(self, monkeypatch):
        import threading

        barrier = threading.Barrier(2, timeout=2)
        account_one_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        account_two_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-two"

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [
                    {
                        "id": account_one_id,
                        "name": "sa-one",
                        "resourceGroup": "rg-data",
                        "location": "westus",
                        "type": "Microsoft.Storage/storageAccounts",
                        "properties": {},
                    },
                    {
                        "id": account_two_id,
                        "name": "sa-two",
                        "resourceGroup": "rg-data",
                        "location": "westus",
                        "type": "Microsoft.Storage/storageAccounts",
                        "properties": {},
                    },
                ]
            if args[:3] == ["storage", "container", "list"]:
                return []
            raise AssertionError(f"unexpected args: {args}")

        def fake_children(subscription_id, account, account_fqdn, account_is_public, account_is_restricted, account_ip_restrictions, account_auth_methods):
            barrier.wait()
            return [{
                "id": f"{account['id']}/blobServices/default/containers/logs",
                "subscription_id": subscription_id,
                "resource_group": account["resourceGroup"],
                "name": "logs",
                "type": "Microsoft.Storage/storageAccounts/blobServices/containers",
                "location": account.get("location"),
                "sku": "blob",
                "tags": json.dumps({}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": account_auth_methods,
                "fqdn": None,
                "pipeline_tag": None,
                "raw_json": json.dumps({"name": "logs"}),
            }]

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))
        monkeypatch.setattr(storage, "_harvest_blob_containers", fake_children)

        rows = storage.harvest("sub-1")
        ids = {row["id"] for row in rows}
        assert account_one_id in ids
        assert account_two_id in ids
        assert f"{account_one_id}/blobServices/default/containers/logs" in ids
        assert f"{account_two_id}/blobServices/default/containers/logs" in ids

    def test_reports_account_progress(self, monkeypatch):
        messages: list[str] = []
        account_one_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        account_two_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-two"

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [
                    {
                        "id": account_one_id,
                        "name": "sa-one",
                        "resourceGroup": "rg-data",
                        "location": "westus",
                        "type": "Microsoft.Storage/storageAccounts",
                        "properties": {},
                    },
                    {
                        "id": account_two_id,
                        "name": "sa-two",
                        "resourceGroup": "rg-data",
                        "location": "westus",
                        "type": "Microsoft.Storage/storageAccounts",
                        "properties": {},
                    },
                ]
            if args[:3] == ["storage", "container", "list"]:
                return []
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))

        rows = storage.harvest("sub-1", progress=messages.append)
        ids = {row["id"] for row in rows}

        assert account_one_id in ids
        assert account_two_id in ids
        assert messages[0] == "discovered 2 storage account(s)"
        assert set(messages[1:]) == {"1/2 storage accounts complete", "2/2 storage accounts complete"}

    def test_blob_children_run_concurrently(self, monkeypatch):
        import threading

        barrier = threading.Barrier(2, timeout=2)
        account_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        container_one_id = f"{account_id}/blobServices/default/containers/logs"
        container_two_id = f"{account_id}/blobServices/default/containers/images"

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [{
                    "id": account_id,
                    "name": "sa-one",
                    "resourceGroup": "rg-data",
                    "location": "westus",
                    "type": "Microsoft.Storage/storageAccounts",
                    "properties": {},
                }]
            if args[:3] == ["storage", "container", "list"]:
                return [
                    {"name": "logs", "publicAccess": "blob"},
                    {"name": "images", "publicAccess": "blob"},
                ]
            if args[:3] == ["storage", "blob", "list"]:
                barrier.wait()
                return [{
                    "name": "hello.txt",
                    "properties": {"accessTier": "Hot", "blobType": "BlockBlob"},
                }]
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))

        rows = storage.harvest("sub-1")
        ids = {row["id"] for row in rows}

        assert account_id in ids
        assert container_one_id in ids
        assert container_two_id in ids
        assert f"{container_one_id}/blobs/hello.txt" in ids
        assert f"{container_two_id}/blobs/hello.txt" in ids

    def test_skips_boot_diagnostics_blob_children(self, monkeypatch):
        account_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        boot_container_name = "bootdiagnostics-prod-0001"
        regular_container_name = "logs"
        blob_calls: list[str] = []

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [{
                    "id": account_id,
                    "name": "sa-one",
                    "resourceGroup": "rg-data",
                    "location": "westus",
                    "type": "Microsoft.Storage/storageAccounts",
                    "properties": {},
                }]
            if args[:3] == ["storage", "container", "list"]:
                return [
                    {"name": boot_container_name, "publicAccess": "blob"},
                    {"name": regular_container_name, "publicAccess": "blob"},
                ]
            if args[:3] == ["storage", "blob", "list"]:
                container_name = args[args.index("--container-name") + 1]
                blob_calls.append(container_name)
                return [{
                    "name": "hello.txt",
                    "properties": {"accessTier": "Hot", "blobType": "BlockBlob"},
                }]
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))

        rows = storage.harvest("sub-1")
        ids = {row["id"] for row in rows}

        assert account_id in ids
        assert f"{account_id}/blobServices/default/containers/{boot_container_name}" in ids
        assert f"{account_id}/blobServices/default/containers/{regular_container_name}" in ids
        assert f"{account_id}/blobServices/default/containers/{regular_container_name}/blobs/hello.txt" in ids
        assert blob_calls == [regular_container_name]

    def test_can_skip_all_blob_children(self, monkeypatch):
        account_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [{
                    "id": account_id,
                    "name": "sa-one",
                    "resourceGroup": "rg-data",
                    "location": "westus",
                    "type": "Microsoft.Storage/storageAccounts",
                    "properties": {},
                }]
            if args[:3] == ["storage", "container", "list"]:
                return [{"name": "logs", "publicAccess": "blob"}]
            if args[:3] == ["storage", "blob", "list"]:
                raise AssertionError("blob listing should be disabled")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))
        monkeypatch.setattr(storage, "_INCLUDE_BLOB_CHILDREN", False)

        rows = storage.harvest("sub-1")
        ids = {row["id"] for row in rows}

        assert account_id in ids
        assert f"{account_id}/blobServices/default/containers/logs" in ids
        assert not any("/blobs/" in row["id"] for row in rows)

    def test_stage_backfill_returns_core_rows_and_backfill_jobs(self, monkeypatch):
        account_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"

        def fake_az(args, subscription_id):
            if args[:3] == ["storage", "account", "list"]:
                return [{
                    "id": account_id,
                    "name": "sa-one",
                    "resourceGroup": "rg-data",
                    "location": "westus",
                    "type": "Microsoft.Storage/storageAccounts",
                    "properties": {},
                }]
            if args[:3] == ["storage", "container", "list"]:
                return [{"name": "logs", "publicAccess": "blob"}]
            if args[:3] == ["storage", "blob", "list"]:
                return [{
                    "name": "hello.txt",
                    "properties": {"accessTier": "Hot", "blobType": "BlockBlob"},
                }]
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(storage, "az", fake_az)
        monkeypatch.setattr(storage, "build_endpoints", lambda entries, timeout=5: json.dumps([]))
        monkeypatch.setattr(storage, "_get_backfill_executor", lambda: _ImmediateExecutor())

        staged = storage.harvest("sub-1", stage_backfill=True)

        assert isinstance(staged, StagedRows)
        assert [row["id"] for row in staged.core_rows] == [account_id]
        assert len(staged.backfill_jobs) == 1

        blob_rows = staged.backfill_jobs[0].future.result()
        ids = {row["id"] for row in blob_rows}

        assert f"{account_id}/blobServices/default/containers/logs" in ids
        assert f"{account_id}/blobServices/default/containers/logs/blobs/hello.txt" in ids


# ---------------------------------------------------------------------------
# extract_ip_restrictions
# ---------------------------------------------------------------------------

class TestExtractIpRestrictions:
    def test_empty_resource_returns_empty_list(self):
        result = extract_ip_restrictions({})
        assert result == []

    def test_returns_list_of_restriction_dicts(self):
        # extract_ip_restrictions takes network_acls dict format (not resource)
        network_acls = {
            "ipRules": [
                {"value": "10.0.0.0/8"},
            ]
        }
        result = extract_ip_restrictions(network_acls=network_acls)
        assert isinstance(result, list)
        assert "10.0.0.0/8" in result


class TestVirtualNetworkHarvest:
    def test_emits_subnet_assets_with_attachment_metadata(self, monkeypatch):
        vnet_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one"
        subnet_id = f"{vnet_id}/subnets/app"

        def fake_az(args, subscription_id):
            assert args == ["network", "vnet", "list"]
            return [{
                "id": vnet_id,
                "name": "vnet-one",
                "resourceGroup": "rg-net",
                "location": "westus",
                "type": "Microsoft.Network/virtualNetworks",
                "properties": {
                    "addressSpace": {"addressPrefixes": ["10.0.0.0/16"]},
                    "subnets": [{
                        "id": subnet_id,
                        "name": "app",
                        "properties": {
                            "addressPrefix": "10.0.1.0/24",
                            "networkSecurityGroup": {"id": "/nsgs/app-nsg", "name": "app-nsg"},
                            "routeTable": {"id": "/routetables/app-rt", "name": "app-rt"},
                            "delegations": [{"properties": {"serviceName": "Microsoft.Web/serverFarms"}}],
                        },
                    }],
                },
            }]

        monkeypatch.setattr(virtual_network, "az", fake_az)
        rows = virtual_network.harvest("sub-1")

        assert {row["id"] for row in rows} == {vnet_id, subnet_id}
        subnet_row = next(row for row in rows if row["id"] == subnet_id)
        extra = json.loads(subnet_row["raw_json"])["_extra"]
        assert extra["parent_vnet_id"] == vnet_id
        assert extra["network_security_group_name"] == "app-nsg"
        assert extra["route_table_name"] == "app-rt"
        assert extra["delegations"] == ["Microsoft.Web/serverFarms"]

class TestVirtualMachineHarvest:
    def test_emits_vms_with_public_ip_metadata(self, monkeypatch):
        vm_id = "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Compute/virtualMachines/vm-one"

        def fake_az(args, subscription_id):
            assert args == ["vm", "list", "-d"]
            return [{
                "id": vm_id,
                "name": "vm-one",
                "resourceGroup": "rg-compute",
                "location": "westus",
                "type": "Microsoft.Compute/virtualMachines",
                "vmSize": "Standard_B2s",
                "publicIps": "20.30.40.50",
                "privateIps": "10.1.0.4",
                "osType": "Linux",
                "powerState": "VM running",
                "properties": {},
            }]

        monkeypatch.setattr(virtual_machine, "az", fake_az)
        rows = virtual_machine.harvest("sub-1")

        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == vm_id
        assert row["is_public"] == 1
        extra = json.loads(row["raw_json"])["_extra"]
        assert extra["public_ips"] == "20.30.40.50"
        assert extra["private_ips"] == "10.1.0.4"
        assert extra["os_type"] == "Linux"


class TestVirtualMachineScaleSetHarvest:
    def test_emits_vnet_and_subnet_metadata(self, monkeypatch):
        vmss_id = "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-one"
        subnet_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app"

        def fake_az(args, subscription_id):
            if args[:3] == ["resource", "list", "--resource-type"]:
                return [{
                    "id": vmss_id,
                    "name": "vmss-one",
                    "resourceGroup": "rg-compute",
                    "location": "westus",
                    "type": "Microsoft.Compute/virtualMachineScaleSets",
                    "sku": {"capacity": 2},
                    "properties": {},
                }]
            if args[:3] == ["resource", "show", "--ids"]:
                return {
                    "id": vmss_id,
                    "name": "vmss-one",
                    "resourceGroup": "rg-compute",
                    "location": "westus",
                    "type": "Microsoft.Compute/virtualMachineScaleSets",
                    "properties": {
                        "virtualMachineProfile": {
                            "networkProfile": {
                                "networkInterfaceConfigurations": [
                                    {
                                        "properties": {
                                            "ipConfigurations": [
                                                {
                                                    "properties": {
                                                        "subnet": {"id": subnet_id},
                                                        "publicIPAddressConfiguration": None,
                                                    }
                                                }
                                            ]
                                        }
                                    }
                                ]
                            }
                        }
                    },
                }
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(virtual_machine_scale_set, "harvest_resource_list", lambda *args, **kwargs: [{
            "id": vmss_id,
            "subscription_id": "sub-1",
            "resource_group": "rg-compute",
            "name": "vmss-one",
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "location": "westus",
            "sku": "2",
            "tags": json.dumps({}),
            "is_public": 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": None,
            "pipeline_tag": None,
            "raw_json": json.dumps({}),
        }])
        monkeypatch.setattr(virtual_machine_scale_set, "az", fake_az)
        rows = virtual_machine_scale_set.harvest("sub-1")

        assert len(rows) == 1
        raw = json.loads(rows[0]["raw_json"])
        extra = raw["_extra"]
        assert extra["subnet_id"] == subnet_id
        assert extra["subnet_name"] == "app"
        assert extra["vnet_name"] == "vnet-one"
        assert extra["vnet_resource_group"] == "rg-net"


# ---------------------------------------------------------------------------
# AKS route model — pure function tests (no az CLI, no DB)
# ---------------------------------------------------------------------------

from Azure.aks import (
    _get_ingress_backend_references,
    _get_ingress_status_addresses,
    _test_selector_matches_labels,
    _get_matching_deployments_for_service,
    _get_deployment_label,
    _build_route_model,
    _make_route_id,
)
from Azure import apim
from Azure.apim import _get_gateway_hosts, _get_apim_exposure_level
from Azure import function_apps
from Azure import service_fabric
from Azure import app_gateway
from Azure import bastion_host
from Azure import virtual_machine_scale_set
from Azure.function_apps import _derive_trigger_auth_methods, _extract_http_triggers
from Azure.firewall import _extract_nat_rules, _extract_app_rules, _get_firewall_exposure_level
from Azure.front_door import _extract_classic_routes, _extract_afd_route


class TestGetIngressStatusAddresses:
    def test_extracts_ip(self):
        ingress = {"status": {"loadBalancer": {"ingress": [{"ip": "10.0.0.1"}]}}}
        assert _get_ingress_status_addresses(ingress) == ["10.0.0.1"]

    def test_extracts_hostname(self):
        ingress = {"status": {"loadBalancer": {"ingress": [{"hostname": "lb.example.com"}]}}}
        assert _get_ingress_status_addresses(ingress) == ["lb.example.com"]

    def test_deduplicates(self):
        ingress = {"status": {"loadBalancer": {"ingress": [
            {"ip": "10.0.0.1"}, {"ip": "10.0.0.1"},
        ]}}}
        assert _get_ingress_status_addresses(ingress) == ["10.0.0.1"]

    def test_empty_status(self):
        assert _get_ingress_status_addresses({}) == []

    def test_missing_load_balancer(self):
        assert _get_ingress_status_addresses({"status": {}}) == []


class TestGetIngressBackendReferences:
    def _make_ingress(self, rules, default_backend=None, host_aliases=None):
        ingress = {
            "metadata": {"namespace": "default", "name": "my-ingress"},
            "spec": {"rules": rules},
        }
        if default_backend:
            ingress["spec"]["defaultBackend"] = default_backend
        if host_aliases:
            ingress["status"] = {"loadBalancer": {"ingress": [{"ip": a} for a in host_aliases]}}
        return ingress

    def test_single_rule_yields_one_ref(self):
        ingress = self._make_ingress([{
            "host": "api.example.com",
            "http": {"paths": [{"path": "/v1", "backend": {"service": {"name": "api-svc", "port": {"number": 80}}}}]},
        }])
        refs = _get_ingress_backend_references([ingress])
        assert len(refs) == 1
        assert refs[0]["host"] == "api.example.com"
        assert refs[0]["service_name"] == "api-svc"
        assert refs[0]["service_port"] == 80
        assert refs[0]["is_default_backend"] is False

    def test_default_backend_yields_ref(self):
        ingress = self._make_ingress([], default_backend={"service": {"name": "fallback-svc", "port": {"number": 8080}}})
        refs = _get_ingress_backend_references([ingress])
        assert len(refs) == 1
        assert refs[0]["service_name"] == "fallback-svc"
        assert refs[0]["is_default_backend"] is True
        assert refs[0]["host"] is None
        assert refs[0]["path"] is None

    def test_named_port_reference(self):
        ingress = self._make_ingress([{
            "host": "app.example.com",
            "http": {"paths": [{"path": "/", "backend": {"service": {"name": "app-svc", "port": {"name": "http"}}}}]},
        }])
        refs = _get_ingress_backend_references([ingress])
        assert refs[0]["service_port"] == "http"

    def test_host_aliases_attached(self):
        ingress = self._make_ingress([{
            "host": "app.example.com",
            "http": {"paths": [{"path": "/", "backend": {"service": {"name": "svc", "port": {"number": 80}}}}]},
        }], host_aliases=["192.168.1.1"])
        refs = _get_ingress_backend_references([ingress])
        assert refs[0]["host_aliases"] == ["192.168.1.1"]

    def test_path_without_backend_skipped(self):
        ingress = self._make_ingress([{
            "host": "app.example.com",
            "http": {"paths": [{"path": "/no-backend"}]},
        }])
        refs = _get_ingress_backend_references([ingress])
        assert len(refs) == 0


class TestTestSelectorMatchesLabels:
    def test_matching_labels(self):
        assert _test_selector_matches_labels({"app": "web"}, {"app": "web", "env": "prod"}) is True

    def test_non_matching_value(self):
        assert _test_selector_matches_labels({"app": "web"}, {"app": "api"}) is False

    def test_missing_label_key(self):
        assert _test_selector_matches_labels({"app": "web"}, {"env": "prod"}) is False

    def test_empty_selector_returns_false(self):
        assert _test_selector_matches_labels({}, {"app": "web"}) is False

    def test_none_selector_returns_false(self):
        assert _test_selector_matches_labels(None, {"app": "web"}) is False

    def test_none_labels_returns_false(self):
        assert _test_selector_matches_labels({"app": "web"}, None) is False

    def test_multiple_selectors_all_must_match(self):
        selector = {"app": "web", "tier": "frontend"}
        assert _test_selector_matches_labels(selector, {"app": "web", "tier": "frontend"}) is True
        assert _test_selector_matches_labels(selector, {"app": "web", "tier": "backend"}) is False


class TestGetMatchingDeploymentsForService:
    def _make_service(self, namespace, selector):
        return {
            "metadata": {"namespace": namespace, "name": "svc"},
            "spec": {"selector": selector},
        }

    def _make_deploy(self, namespace, pod_labels):
        return {
            "metadata": {"namespace": namespace, "name": "deploy"},
            "spec": {"template": {"metadata": {"labels": pod_labels}}},
        }

    def test_matches_deployment_in_same_namespace(self):
        svc = self._make_service("default", {"app": "web"})
        deploy = self._make_deploy("default", {"app": "web"})
        assert _get_matching_deployments_for_service(svc, [deploy]) == [deploy]

    def test_ignores_different_namespace(self):
        svc = self._make_service("default", {"app": "web"})
        deploy = self._make_deploy("other-ns", {"app": "web"})
        assert _get_matching_deployments_for_service(svc, [deploy]) == []

    def test_empty_selector_matches_nothing(self):
        svc = self._make_service("default", {})
        deploy = self._make_deploy("default", {"app": "web"})
        assert _get_matching_deployments_for_service(svc, [deploy]) == []

    def test_none_selector_matches_nothing(self):
        svc = self._make_service("default", None)
        deploy = self._make_deploy("default", {"app": "web"})
        assert _get_matching_deployments_for_service(svc, [deploy]) == []


class TestGetDeploymentLabel:
    def _make_deploy(self, pod_labels=None, meta_labels=None):
        return {
            "metadata": {"labels": meta_labels or {}},
            "spec": {"template": {"metadata": {"labels": pod_labels or {}}}},
        }

    def test_prefers_pod_template_label(self):
        deploy = self._make_deploy(pod_labels={"git_repository": "my-repo"}, meta_labels={"git_repository": "old-repo"})
        assert _get_deployment_label(deploy, "git_repository") == "my-repo"

    def test_falls_back_to_metadata_label(self):
        deploy = self._make_deploy(meta_labels={"team": "platform"})
        assert _get_deployment_label(deploy, "team") == "platform"

    def test_returns_none_when_absent(self):
        deploy = self._make_deploy()
        assert _get_deployment_label(deploy, "git_repository") is None


class TestBuildRouteModel:
    def _make_cluster(self):
        return {"id": "/subscriptions/sub/rg/rg1/aks/cl1", "name": "cl1", "resourceGroup": "rg1"}

    def _make_ingress(self, host, path, svc_name, host_aliases=None):
        ingress = {
            "metadata": {"namespace": "default", "name": "ing1"},
            "spec": {"rules": [{
                "host": host,
                "http": {"paths": [{"path": path, "backend": {"service": {"name": svc_name, "port": {"number": 80}}}}]},
            }]},
        }
        if host_aliases:
            ingress["status"] = {
                "loadBalancer": {
                    "ingress": [
                        {"ip": alias} if _is_ip_address(alias) else {"hostname": alias}
                        for alias in host_aliases
                    ]
                }
            }
        return ingress

    def _make_service(self, name, selector):
        return {
            "metadata": {"namespace": "default", "name": name},
            "spec": {"selector": selector, "ports": [{"port": 80}]},
        }

    def _make_deployment(self, pod_labels):
        return {
            "metadata": {"namespace": "default", "name": "deploy1", "labels": {}},
            "spec": {"template": {"metadata": {"labels": pod_labels}}},
        }

    def test_full_chain_produces_route(self):
        ingress = self._make_ingress("api.example.com", "/v1", "api-svc")
        service = self._make_service("api-svc", {"app": "api"})
        deploy = self._make_deployment({"app": "api", "git_repository": "my-repo", "team": "platform"})
        routes = _build_route_model(self._make_cluster(), [ingress], [service], [deploy])
        assert len(routes) == 1
        assert routes[0]["host"] == "api.example.com"
        assert routes[0]["git_repository"] == "my-repo"
        assert routes[0]["team"] == "platform"
        assert routes[0]["service_name"] == "api-svc"

    def test_private_ingress_marks_route_internal(self):
        ingress = self._make_ingress("api.example.com", "/v1", "api-svc", host_aliases=["10.0.0.1"])
        service = self._make_service("api-svc", {"app": "api"})
        deploy = self._make_deployment({"app": "api", "git_repository": "my-repo", "team": "platform"})
        routes = _build_route_model(self._make_cluster(), [ingress], [service], [deploy])
        assert len(routes) == 1
        assert routes[0]["exposure_level"] == "Internal"

    def test_missing_git_repository_excluded(self):
        ingress = self._make_ingress("api.example.com", "/v1", "api-svc")
        service = self._make_service("api-svc", {"app": "api"})
        deploy = self._make_deployment({"app": "api", "team": "platform"})  # no git_repository
        routes = _build_route_model(self._make_cluster(), [ingress], [service], [deploy])
        assert routes == []

    def test_missing_team_excluded(self):
        ingress = self._make_ingress("api.example.com", "/v1", "api-svc")
        service = self._make_service("api-svc", {"app": "api"})
        deploy = self._make_deployment({"app": "api", "git_repository": "my-repo"})  # no team
        routes = _build_route_model(self._make_cluster(), [ingress], [service], [deploy])
        assert routes == []

    def test_no_matching_service_excluded(self):
        ingress = self._make_ingress("api.example.com", "/v1", "missing-svc")
        service = self._make_service("other-svc", {"app": "api"})
        deploy = self._make_deployment({"app": "api", "git_repository": "repo", "team": "team"})
        routes = _build_route_model(self._make_cluster(), [ingress], [service], [deploy])
        assert routes == []


class TestAksHarvestRoutesPersistence:
    def test_persists_route_exposure(self, monkeypatch):
        cluster = {
            "id": "/subscriptions/sub-1/resourceGroups/rg1/providers/Microsoft.ContainerService/managedClusters/cluster1",
            "name": "cluster1",
            "resourceGroup": "rg1",
        }
        ingress = {
            "metadata": {"namespace": "default", "name": "ing1"},
            "spec": {
                "rules": [{
                    "host": "api.example.com",
                    "http": {"paths": [{"path": "/v1", "backend": {"service": {"name": "api-svc", "port": {"number": 80}}}}]},
                }],
            },
            "status": {"loadBalancer": {"ingress": [{"ip": "10.0.0.1"}]}},
        }
        service = {
            "metadata": {"namespace": "default", "name": "api-svc"},
            "spec": {"selector": {"app": "api"}, "ports": [{"port": 80}]},
        }
        deployment = {
            "metadata": {"namespace": "default", "name": "deploy1", "labels": {}},
            "spec": {"template": {"metadata": {"labels": {"app": "api", "git_repository": "my-repo", "team": "platform"}}}},
        }

        def fake_az(args, subscription_id):
            if args == ["aks", "list"]:
                return [cluster]
            return []

        def fake_get_cluster_portal_fqdn(cluster_id):
            assert cluster_id == cluster["id"]
            return "cluster1.portal.azure"

        def fake_get_kubernetes_resources(portal_fqdn, resource_type):
            assert portal_fqdn == "cluster1.portal.azure"
            return {
                "ingresses": [ingress],
                "services": [service],
                "deployments": [deployment],
            }[resource_type]

        monkeypatch.setattr(aks, "az", fake_az)
        monkeypatch.setattr(aks, "_get_cluster_portal_fqdn", fake_get_cluster_portal_fqdn)
        monkeypatch.setattr(aks, "_get_kubernetes_resources", fake_get_kubernetes_resources)

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets._ensure_schema(conn)
            count = aks.harvest_routes("sub-1", conn, dry_run=False)
            row = conn.execute(
                "SELECT exposure_level, path FROM aks_routes WHERE cluster_name = ?",
                ("cluster1",),
            ).fetchone()
        finally:
            conn.close()

        assert count == 1
        assert row == ("Internal", "/v1")


class TestMakeRouteId:
    def test_stable_for_same_inputs(self):
        id1 = _make_route_id("cl1", "default", "ing1", "host.com", "/path", "svc", 80, "deploy1", 0)
        id2 = _make_route_id("cl1", "default", "ing1", "host.com", "/path", "svc", 80, "deploy1", 0)
        assert id1 == id2

    def test_null_host_normalized_to_wildcard(self):
        route_id = _make_route_id("cl1", "ns", "ing", None, "/", "svc", 80, "d", 0)
        assert "::*::" in route_id

    def test_null_path_normalized_to_slash(self):
        route_id = _make_route_id("cl1", "ns", "ing", "h", None, "svc", 80, "d", 0)
        assert "::/:" in route_id

    def test_default_backend_flag_in_id(self):
        default_id = _make_route_id("cl1", "ns", "ing", None, None, "svc", 80, "d", 1)
        rule_id = _make_route_id("cl1", "ns", "ing", None, None, "svc", 80, "d", 0)
        assert default_id != rule_id
        assert "default" in default_id
        assert "rule" in rule_id


class TestApimGatewayHosts:
    def test_prefers_hostname_configurations_and_deduplicates(self):
        service = {
            "properties": {
                "hostnameConfigurations": [
                    {"hostName": "api.contoso.com"},
                    {"hostName": "api.contoso.com"},
                    {"hostName": "gateway.azure-api.net"},
                ],
                "gatewayUrl": "https://gateway.azure-api.net/",
            }
        }
        assert _get_gateway_hosts(service) == ["api.contoso.com", "gateway.azure-api.net"]

    def test_falls_back_to_gateway_url(self):
        service = {"properties": {"gatewayUrl": "https://fallback.azure-api.net/"}}
        assert _get_gateway_hosts(service) == ["fallback.azure-api.net"]


class TestApimExposureLevel:
    def test_internal_for_internal_vnet(self):
        assert _get_apim_exposure_level({"properties": {"virtualNetworkType": "Internal"}}) == "Internal"

    def test_internal_for_disabled_public_network_access(self):
        assert _get_apim_exposure_level({"properties": {"publicNetworkAccess": "Disabled"}}) == "Internal"

    def test_public_otherwise(self):
        assert _get_apim_exposure_level({"properties": {"virtualNetworkType": "External"}}) == "Public"


class TestAppGatewayPublicIpDetection:
    def test_detects_public_frontend_from_public_ip_id(self):
        props = {
            "frontendIPConfigurations": [
                {"properties": {"publicIPAddressId": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one"}}
            ]
        }
        assert app_gateway._has_public_frontend(props) == 1
        assert app_gateway._extract_public_ip_ids(props) == [
            "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one"
        ]


class TestBastionPublicIpDetection:
    def test_detects_public_via_ip_configuration_reference(self):
        resource = {
            "properties": {
                "ipConfigurations": [
                    {"properties": {"publicIPAddress": {"id": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one"}}}
                ]
            }
        }
        assert bastion_host._is_public(resource) is True
        assert bastion_host._extract_public_ip_ids(resource) == [
            "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/publicIPAddresses/pip-one"
        ]

    def test_harvest_uses_bastion_show_details_for_public_detection(self, monkeypatch):
        bastion_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/bastionHosts/bastion-one"
        pip_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/publicIPAddresses/bastion-one"

        list_row = {
            "id": bastion_id,
            "name": "bastion-one",
            "resourceGroup": "rg-net",
            "type": "Microsoft.Network/bastionHosts",
            "location": "ukwest",
            "properties": {"ipConfigurations": []},
        }
        detailed_row = {
            **list_row,
            "properties": {
                "ipConfigurations": [
                    {"properties": {"publicIPAddress": {"id": pip_id}}}
                ],
                "dnsSettings": {"fqdn": "bastion-one.example.contoso.com"},
            },
        }

        def fake_az(args, subscription_id):
            if args[:3] == ["resource", "list", "--resource-type"]:
                return [list_row]
            if args[:3] == ["network", "bastion", "show"]:
                return detailed_row
            return []

        monkeypatch.setattr(bastion_host, "az", fake_az)

        rows = bastion_host.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == bastion_id
        assert row["is_public"] == 1
        raw = json.loads(row["raw_json"])
        assert raw["_extra"]["public_ip_resource_ids"] == [pip_id]
        assert row["fqdn"] == "bastion-one.example.contoso.com"


class TestVmssPublicIpDetection:
    def test_detects_public_ip_configuration_in_network_profile(self):
        resource = {
            "properties": {
                "virtualMachineProfile": {
                    "networkProfile": {
                        "networkInterfaceConfigurations": [
                            {
                                "properties": {
                                    "ipConfigurations": [
                                        {"properties": {"publicIPAddressConfiguration": {"name": "pip-config"}}}
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
        assert virtual_machine_scale_set._has_public_ip_configuration(resource) is True


class TestApimHarvestConcurrency:
    def test_counts_apim_services_in_parallel(self, monkeypatch):
        barrier = threading.Barrier(2)
        services = [
            {
                "id": "svc-1",
                "name": "apim-one",
                "resourceGroup": "rg-one",
                "type": "Microsoft.ApiManagement/service",
                "location": "westeurope",
                "properties": {
                    "gatewayUrl": "https://one.azure-api.net/",
                    "portalUrl": "https://portal.one.azure-api.net/",
                    "virtualNetworkType": "External",
                },
            },
            {
                "id": "svc-2",
                "name": "apim-two",
                "resourceGroup": "rg-two",
                "type": "Microsoft.ApiManagement/service",
                "location": "westeurope",
                "properties": {
                    "gatewayUrl": "https://two.azure-api.net/",
                    "portalUrl": "https://portal.two.azure-api.net/",
                    "virtualNetworkType": "External",
                },
            },
        ]

        def fake_az(args, subscription_id):
            if args == ["apim", "list"]:
                return services
            return []

        def fake_build_endpoints(entries, timeout=5):
            barrier.wait(timeout=2)
            return json.dumps([])

        monkeypatch.setattr(apim, "az", fake_az)
        monkeypatch.setattr(apim, "build_endpoints", fake_build_endpoints)

        assets = apim.harvest("sub-1")
        names = {asset["name"] for asset in assets}
        counts = [json.loads(asset["raw_json"])["_extra"]["api_count"] for asset in assets]

        assert names == {"apim-one", "apim-two"}
        assert counts == [None, None]


class TestApimRouteHarvestConcurrency:
    def test_harvest_routes_emits_progress_bars(self, monkeypatch, capsys):
        barrier = threading.Barrier(2)
        service = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-one/providers/Microsoft.ApiManagement/service/orders-apim",
            "name": "orders-apim",
            "resourceGroup": "rg-one",
            "type": "Microsoft.ApiManagement/service",
            "properties": {
                "gatewayUrl": "https://api.contoso.test/",
                "portalUrl": "https://portal.contoso.test/",
                "virtualNetworkType": "External",
            },
        }
        apis = [
            {
                "name": "orders",
                "properties": {
                    "displayName": "Orders API",
                    "path": "orders",
                    "serviceUrl": "https://orders-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
            {
                "name": "customers",
                "properties": {
                    "displayName": "Customers API",
                    "path": "customers",
                    "serviceUrl": "https://customers-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
        ]

        def fake_az(args, subscription_id):
            if args == ["apim", "list"]:
                return [service]
            return []

        def fake_list_apis(service_name, resource_group, subscription_id):
            return apis

        def fake_list_operations(service_name, resource_group, api_id, subscription_id):
            return [
                {
                    "name": f"{api_id}-get",
                    "displayName": f"Get {api_id}",
                    "method": "get",
                    "urlTemplate": "/",
                }
            ]

        def fake_show_policy(resource_kind, service_name, resource_group, subscription_id, *, api_id=None, operation_id=None):
            if resource_kind == "api":
                barrier.wait(timeout=2)
            return None

        monkeypatch.setattr(apim, "az", fake_az)
        monkeypatch.setattr(apim, "_az_list_apis", fake_list_apis)
        monkeypatch.setattr(apim, "_az_list_operations", fake_list_operations)
        monkeypatch.setattr(apim, "_az_show_policy", fake_show_policy)

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets._ensure_schema(conn)
            apim._ensure_apim_schema(conn)
            route_count = apim.harvest_routes("sub-1", conn, dry_run=False)
        finally:
            conn.close()

        out = capsys.readouterr().out
        assert route_count == 2
        assert "[apim-routes] services" in out
        assert "[apim-routes] orders-apim APIs" in out
        assert re.search(r"\[[#-]{8,}\]", out), out

    def test_processes_api_policies_in_parallel(self, monkeypatch):
        barrier = threading.Barrier(2)
        service = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-one/providers/Microsoft.ApiManagement/service/orders-apim",
            "name": "orders-apim",
            "resourceGroup": "rg-one",
            "type": "Microsoft.ApiManagement/service",
            "properties": {
                "gatewayUrl": "https://api.contoso.test/",
                "portalUrl": "https://portal.contoso.test/",
                "virtualNetworkType": "External",
            },
        }
        apis = [
            {
                "name": "orders",
                "properties": {
                    "displayName": "Orders API",
                    "path": "orders",
                    "serviceUrl": "https://orders-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
            {
                "name": "customers",
                "properties": {
                    "displayName": "Customers API",
                    "path": "customers",
                    "serviceUrl": "https://customers-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
        ]

        def fake_az(args, subscription_id):
            if args == ["apim", "list"]:
                return [service]
            return []

        def fake_list_apis(service_name, resource_group, subscription_id):
            return apis

        def fake_list_operations(service_name, resource_group, api_id, subscription_id):
            return [
                {
                    "name": f"{api_id}-get",
                    "displayName": f"Get {api_id}",
                    "method": "get",
                    "urlTemplate": "/",
                }
            ]

        def fake_show_policy(resource_kind, service_name, resource_group, subscription_id, *, api_id=None, operation_id=None):
            if resource_kind == "api":
                barrier.wait(timeout=2)
            return None

        monkeypatch.setattr(apim, "az", fake_az)
        monkeypatch.setattr(apim, "_az_list_apis", fake_list_apis)
        monkeypatch.setattr(apim, "_az_list_operations", fake_list_operations)
        monkeypatch.setattr(apim, "_az_show_policy", fake_show_policy)

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets._ensure_schema(conn)
            apim._ensure_apim_schema(conn)
            route_count = apim.harvest_routes("sub-1", conn, dry_run=False)
            route_rows = conn.execute("SELECT COUNT(*) FROM apim_api_routes").fetchone()[0]
            op_rows = conn.execute("SELECT COUNT(*) FROM apim_api_operations").fetchone()[0]
        finally:
            conn.close()

        assert route_count == 2
        assert route_rows == 2
        assert op_rows == 2

    def test_stages_operation_policy_backfill(self, monkeypatch):
        service = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-one/providers/Microsoft.ApiManagement/service/orders-apim",
            "name": "orders-apim",
            "resourceGroup": "rg-one",
            "type": "Microsoft.ApiManagement/service",
            "properties": {
                "gatewayUrl": "https://api.contoso.test/",
                "portalUrl": "https://portal.contoso.test/",
                "virtualNetworkType": "External",
            },
        }
        apis = [
            {
                "name": "orders",
                "properties": {
                    "displayName": "Orders API",
                    "path": "orders",
                    "serviceUrl": "https://orders-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
            {
                "name": "customers",
                "properties": {
                    "displayName": "Customers API",
                    "path": "customers",
                    "serviceUrl": "https://customers-backend.contoso.test/v1",
                    "subscriptionRequired": True,
                    "protocols": ["https"],
                },
            },
        ]

        def fake_az(args, subscription_id):
            if args == ["apim", "list"]:
                return [service]
            return []

        def fake_list_apis(service_name, resource_group, subscription_id):
            return apis

        def fake_list_operations(service_name, resource_group, api_id, subscription_id):
            return [
                {
                    "name": f"{api_id}-get",
                    "displayName": f"Get {api_id}",
                    "method": "get",
                    "urlTemplate": "/",
                }
            ]

        monkeypatch.setattr(apim, "az", fake_az)
        monkeypatch.setattr(apim, "_az_list_apis", fake_list_apis)
        monkeypatch.setattr(apim, "_az_list_operations", fake_list_operations)
        monkeypatch.setattr(apim, "_az_show_policy", lambda *args, **kwargs: None)
        monkeypatch.setattr(apim, "_get_backfill_executor", lambda: _ImmediateExecutor())

        conn = sqlite3.connect(":memory:")
        try:
            apim._ensure_apim_schema(conn)
            staged = apim.harvest_routes("sub-1", conn, dry_run=False, stage_backfill=True)
        finally:
            conn.close()

        assert isinstance(staged, StagedRows)
        assert len(staged.core_rows) == 2
        assert len(staged.backfill_jobs) == 2

        op_rows = [job.future.result() for job in staged.backfill_jobs]
        ids = {row["id"] for row in op_rows}

        assert ids == {
            "orders-apim::orders::orders-get",
            "orders-apim::customers::customers-get",
        }


class TestApimRoutingMapConcurrency:
    def test_processes_api_operations_lookup_in_parallel(self, monkeypatch):
        barrier = threading.Barrier(2)
        apim_instance = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-one/providers/Microsoft.ApiManagement/service/orders-apim",
            "name": "orders-apim",
            "resourceGroup": "rg-one",
            "type": "Microsoft.ApiManagement/service",
        }
        apis = [
            {
                "name": "orders",
                "displayName": "Orders API",
                "path": "orders",
                "serviceUrl": "https://orders-backend.contoso.test/v1",
                "subscriptionRequired": True,
                "protocols": ["https"],
            },
            {
                "name": "customers",
                "displayName": "Customers API",
                "path": "customers",
                "serviceUrl": "https://customers-backend.contoso.test/v1",
                "subscriptionRequired": True,
                "protocols": ["https"],
            },
        ]

        def fake_list_backends(apim_name, resource_group, subscription_id):
            return []

        def fake_list_apis(apim_name, resource_group, subscription_id):
            return apis

        def fake_list_operations(apim_name, resource_group, api_id, subscription_id):
            barrier.wait(timeout=5)
            return [
                {
                    "name": f"{api_id}-get",
                    "displayName": f"Get {api_id}",
                    "method": "get",
                    "urlTemplate": "/",
                }
            ]

        monkeypatch.setattr(apim_routing_map, "list_backends", fake_list_backends)
        monkeypatch.setattr(apim_routing_map, "list_apis", fake_list_apis)
        monkeypatch.setattr(apim_routing_map, "list_operations", fake_list_operations)

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provisioned_assets (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    name TEXT,
                    type TEXT,
                    fqdn TEXT
                );
                CREATE TABLE IF NOT EXISTS resources (
                    id INTEGER PRIMARY KEY,
                    experiment_id TEXT,
                    resource_name TEXT,
                    resource_type TEXT
                );
                """
            )
            apim_routing_map._ensure_apim_schema(conn)
            route_count = apim_routing_map.process_apim(apim_instance, "sub-1", conn, dry_run=False)
            route_rows = conn.execute("SELECT COUNT(*) FROM apim_api_routes").fetchone()[0]
            op_rows = conn.execute("SELECT COUNT(*) FROM apim_api_operations").fetchone()[0]
        finally:
            conn.close()

        assert route_count == 2
        assert route_rows == 2
        assert op_rows == 2


class TestFunctionTriggerParsing:
    def test_extracts_http_trigger_fields(self):
        functions = [{
            "name": "myapp/RunReport",
            "properties": {
                "config": {
                    "bindings": [
                        {"type": "httpTrigger", "route": "reports/run", "authLevel": "anonymous", "methods": ["get", "post"]},
                        {"type": "http"},
                    ]
                }
            },
        }]
        triggers = _extract_http_triggers(functions)
        assert triggers == [{
            "function_name": "RunReport",
            "route": "reports/run",
            "auth_level": "anonymous",
            "methods": ["GET", "POST"],
        }]

    def test_skips_non_http_and_missing_bindings(self):
        functions = [
            {"name": "myapp/QueueOnly", "properties": {"config": {"bindings": [{"type": "queueTrigger"}]}}},
            {"name": "myapp/NoConfig", "properties": {}},
        ]
        assert _extract_http_triggers(functions) == []

    def test_defaults_route_to_function_name(self):
        functions = [{
            "name": "myapp/DefaultRoute",
            "config": {"bindings": [{"type": "httpTrigger", "authLevel": "function"}]},
        }]
        triggers = _extract_http_triggers(functions)
        assert triggers[0]["route"] == "DefaultRoute"
        assert triggers[0]["auth_level"] == "function"
        assert triggers[0]["methods"] == []

    def test_trigger_auth_methods_mark_auth_required_without_anonymous(self):
        triggers = [
            {"function_name": "A", "route": "a", "auth_level": "function", "methods": ["GET"]},
            {"function_name": "B", "route": "b", "auth_level": "admin", "methods": ["POST"]},
        ]
        methods = _derive_trigger_auth_methods(triggers)
        assert "function_http_auth_required" in methods
        assert "function_http_anonymous" not in methods
        assert "function_key" in methods

    def test_trigger_auth_methods_mark_anonymous_when_present(self):
        triggers = [
            {"function_name": "A", "route": "a", "auth_level": "anonymous", "methods": ["GET"]},
            {"function_name": "B", "route": "b", "auth_level": "function", "methods": ["POST"]},
        ]
        methods = _derive_trigger_auth_methods(triggers)
        assert "function_http_anonymous" in methods
        assert "function_http_auth_required" not in methods

    def test_az_list_functions_parses_output_with_warning_noise(self, monkeypatch):
        class _Result:
            returncode = 0
            stdout = "WARNING: preview command\\n[{\"name\":\"app/Fn\",\"config\":{\"bindings\":[{\"type\":\"httpTrigger\",\"authLevel\":\"Function\"}]}}]"
            stderr = ""

        monkeypatch.setattr(function_apps.subprocess, "run", lambda *args, **kwargs: _Result())
        rows = function_apps._az_list_functions("app", "rg", "sub")
        assert isinstance(rows, list)
        assert rows and rows[0]["name"] == "app/Fn"

    def test_az_list_functions_degrades_on_cli_json_parse_error(self, monkeypatch):
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "Failed: JSON.parse: unexpected character at line 1 column 1 of the JSON data"

        monkeypatch.setattr(function_apps.subprocess, "run", lambda *args, **kwargs: _Result())
        rows = function_apps._az_list_functions("app", "rg", "sub")
        assert rows == []


class TestServiceFabricHarvest:
    def test_harvest_extracts_applications_and_services(self, monkeypatch):
        cluster = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-sf/providers/Microsoft.ServiceFabric/clusters/sfha",
            "name": "sfha",
            "resourceGroup": "rg-sf",
            "location": "uksouth",
            "type": "Microsoft.ServiceFabric/clusters",
            "tags": {"pipeline": "sf-pipeline"},
            "properties": {
                "managementEndpoint": "https://sfha.example.com:19080",
                "clusterState": "Ready",
                "clusterCodeVersion": "11.4.205.1",
                "nodeTypes": [{"name": "nt1"}],
                "reliabilityLevel": "Silver",
                "upgradeMode": "Manual",
            },
        }
        monkeypatch.setattr(service_fabric, "az", lambda args, subscription_id: [cluster])

        class _Result:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        def fake_run(cmd, capture_output, text, encoding, errors, timeout):
            if cmd[:4] == ["az", "sf", "application", "list"]:
                return _Result(json.dumps([
                    {
                        "id": "/clusters/sfha/applications/fabric:/OrderApp",
                        "name": "fabric:/OrderApp",
                        "properties": {"typeName": "OrderAppType"},
                    }
                ]))
            if cmd[:4] == ["az", "sf", "service", "list"]:
                return _Result(json.dumps([
                    {
                        "id": "/clusters/sfha/services/fabric:/OrderApp/OrderService",
                        "name": "fabric:/OrderApp/OrderService",
                        "properties": {
                            "serviceTypeName": "OrderServiceType",
                            "serviceStatus": "Active",
                            "healthState": "Ok",
                        },
                    }
                ]))
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(service_fabric.subprocess, "run", fake_run)

        rows = service_fabric.harvest("sub-1")
        types = {r["type"] for r in rows}
        assert "Microsoft.ServiceFabric/clusters" in types
        assert "Microsoft.ServiceFabric/clusters/applications" in types
        assert "Microsoft.ServiceFabric/clusters/services" in types
        svc = next(r for r in rows if r["type"] == "Microsoft.ServiceFabric/clusters/services")
        assert svc["name"] == "fabric:/OrderApp/OrderService"
        assert svc["resource_group"] == "rg-sf"
        assert svc["fqdn"] == "sfha.example.com"


class TestFirewallNatRules:
    def test_extracts_destination_and_translation_fields(self):
        collections = [{
            "name": "nat-collection",
            "rules": [{
                "name": "ssh",
                "destinationAddresses": ["20.1.1.1"],
                "translatedAddress": "10.0.0.4",
                "translatedPort": "22",
                "ipProtocols": ["TCP"],
            }],
        }]
        rules = _extract_nat_rules(collections, "fw1", "rg1", "Public")
        assert rules[0]["entry_hosts"] == ["20.1.1.1"]
        assert rules[0]["translated_address"] == "10.0.0.4"
        assert rules[0]["translated_fqdn"] is None
        assert rules[0]["protocols"] == ["TCP"]

    def test_supports_translated_fqdn_and_skips_incomplete_rules(self):
        collections = [{
            "name": "nat-collection",
            "rules": [
                {"name": "web", "destinationAddress": "20.1.1.2", "translatedFqdn": "internal.contoso.local"},
                {"name": "skip-no-entry", "translatedAddress": "10.0.0.5"},
                {"name": "skip-no-target", "destinationAddress": "20.1.1.3"},
            ],
        }]
        rules = _extract_nat_rules(collections, "fw1", "rg1", "Public")
        assert len(rules) == 1
        assert rules[0]["translated_fqdn"] == "internal.contoso.local"
        assert rules[0]["entry_hosts"] == ["20.1.1.2"]


class TestFirewallAppRules:
    def test_extracts_targets_and_protocols(self):
        collections = [{
            "name": "app-collection",
            "rules": [{
                "name": "allow-web",
                "sourceAddresses": ["10.0.0.0/24"],
                "targetFqdns": ["github.com", "api.github.com"],
                "protocols": [{"protocolType": "Https", "port": 443}, {"protocolType": "Http", "port": 80}],
            }],
        }]
        rules = _extract_app_rules(collections, "fw1", "rg1")
        assert rules == [{
            "id": "fw1::app-collection::allow-web",
            "firewall_name": "fw1",
            "resource_group": "rg1",
            "collection_name": "app-collection",
            "rule_name": "allow-web",
            "source_addresses": ["10.0.0.0/24"],
            "target_fqdns": ["github.com", "api.github.com"],
            "protocols": ["Https:443", "Http:80"],
        }]


class TestFirewallExposureLevel:
    def test_public_if_public_ip_present(self):
        firewall = {"properties": {"ipConfigurations": [{"properties": {"publicIPAddress": {"id": "/pip/one"}}}]}}
        assert _get_firewall_exposure_level(firewall) == "Public"

    def test_public_if_public_ip_id_present(self):
        firewall = {"properties": {"ipConfigurations": [{"properties": {"publicIPAddressId": "/pip/one"}}]}}
        assert _get_firewall_exposure_level(firewall) == "Public"

    def test_internal_without_public_ip(self):
        firewall = {"properties": {"ipConfigurations": [{"properties": {}}]}}
        assert _get_firewall_exposure_level(firewall) == "Internal"


class TestFirewallPolicyHarvest:
    def test_persists_policy_summary_and_rule_counts(self, monkeypatch):
        from Scripts.Harvest.Azure import firewall

        fw = {
            "name": "fw-one",
            "resourceGroup": "rg-net",
            "properties": {
                "firewallPolicy": {"id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/firewallPolicies/policy-one"},
            },
        }
        policy_stub = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/firewallPolicies/policy-one",
            "name": "policy-one",
            "resourceGroup": "rg-net",
        }
        policy_detail = {
            "properties": {
                "policySettings": {
                    "mode": "Detection",
                    "threatIntelMode": "Alert",
                },
                "dnsSettings": {"enableProxy": True},
            }
        }
        rule_groups = {
            "value": [
                {
                    "name": "group-one",
                    "properties": {
                        "priority": 100,
                        "ruleCollections": [
                            {
                                "name": "nat-collection",
                                "properties": {
                                    "priority": 100,
                                    "ruleCollectionType": "FirewallPolicyNatRuleCollection",
                                    "action": "Dnat",
                                    "rules": [
                                        {"name": "nat-1"},
                                        {"name": "nat-2"},
                                    ],
                                },
                            }
                        ],
                    },
                }
            ]
        }

        def fake_az(args, subscription_id):
            if args == ["network", "firewall", "list"]:
                return [fw]
            if args == ["network", "firewall", "policy", "list"]:
                return [policy_stub]
            if args == ["network", "firewall", "policy", "show", "--name", "policy-one", "--resource-group", "rg-net"]:
                return policy_detail
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(firewall, "az", fake_az)
        monkeypatch.setattr(firewall, "_az_show", lambda args, subscription_id, timeout=120: policy_detail if args[:4] == ["network", "firewall", "policy", "show"] else None)
        monkeypatch.setattr(firewall, "_az_rest", lambda url: rule_groups)

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE firewall_nat_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    firewall_name TEXT,
                    resource_group TEXT,
                    collection_name TEXT,
                    rule_name TEXT,
                    entry_hosts TEXT,
                    translated_address TEXT,
                    translated_fqdn TEXT,
                    translated_port TEXT,
                    protocols TEXT,
                    exposure_level TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE firewall_app_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    firewall_name TEXT,
                    resource_group TEXT,
                    collection_name TEXT,
                    rule_name TEXT,
                    source_addresses TEXT,
                    target_fqdns TEXT,
                    protocols TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE firewall_policies (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    name TEXT,
                    resource_group TEXT,
                    associated_firewalls TEXT,
                    mode TEXT,
                    threat_intelligence_mode TEXT,
                    dns_proxy_enabled INTEGER,
                    rule_collection_groups TEXT,
                    nat_rule_count INTEGER,
                    app_rule_count INTEGER,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO firewall_nat_rules (id, subscription_id, firewall_name, resource_group) VALUES (?, ?, ?, ?)",
                ("nat-1", "sub-1", "fw-one", "rg-net"),
            )
            conn.execute(
                "INSERT INTO firewall_app_rules (id, subscription_id, firewall_name, resource_group) VALUES (?, ?, ?, ?)",
                ("app-1", "sub-1", "fw-one", "rg-net"),
            )

            count = firewall.harvest_policies("sub-1", conn, dry_run=False)
            assert count == 1
            row = conn.execute(
                "SELECT name, associated_firewalls, mode, threat_intelligence_mode, dns_proxy_enabled, nat_rule_count, app_rule_count, rule_collection_groups FROM firewall_policies WHERE name = ?",
                ("policy-one",),
            ).fetchone()
            assert row[0] == "policy-one"
            assert json.loads(row[1]) == ["fw-one"]
            assert row[2] == "Detection"
            assert row[3] == "Alert"
            assert row[4] == 1
            assert row[5] == 1
            assert row[6] == 1
            assert json.loads(row[7])[0]["collections"][0]["rule_count"] == 2
        finally:
            conn.close()


class TestHarvestSubscription:
    def test_invokes_appgw_routing_map_harvest(self, monkeypatch):
        calls = []

        def fake_harvest_routing(subscription_id, conn, dry_run=False):
            calls.append((subscription_id, dry_run))
            return 0, 0, 0, 0

        monkeypatch.setattr(harvest_azure_assets, "PROVIDERS", [])
        monkeypatch.setattr(harvest_azure_assets.appgw_routing_map, "harvest_routing", fake_harvest_routing)

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets.harvest_subscription({"id": "sub-1", "name": "sub"}, conn, dry_run=True)
        finally:
            conn.close()

        assert calls == [("sub-1", True)]


class TestHarvestSubscriptionParallelism:
    def test_runs_provider_harvesters_in_parallel(self, monkeypatch):
        barrier = threading.Barrier(2, timeout=2)
        started: list[str] = []

        def fake_first(subscription_id):
            started.append("first")
            barrier.wait(timeout=2)
            return [{"id": "first", "subscription_id": subscription_id}]

        def fake_second(subscription_id):
            started.append("second")
            barrier.wait(timeout=2)
            return [{"id": "second", "subscription_id": subscription_id}]

        monkeypatch.setattr(
            harvest_azure_assets,
            "PROVIDERS",
            [("Storage", fake_first), ("SQL Servers", fake_second)],
        )
        monkeypatch.setattr(harvest_azure_assets.appgw_routing_map, "harvest_routing", lambda *args, **kwargs: (0, 0, 0, 0))
        monkeypatch.setattr(harvest_azure_assets.aks, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.apim, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.apim_routing_map, "harvest_backends", lambda *args, **kwargs: (0, 0))
        monkeypatch.setattr(harvest_azure_assets.function_apps, "harvest_http_triggers", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.front_door, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.firewall, "harvest_rules", lambda *args, **kwargs: (0, 0))

        conn = sqlite3.connect(":memory:")
        try:
            total = harvest_azure_assets.harvest_subscription({"id": "sub-1", "name": "sub"}, conn, dry_run=True)
        finally:
            conn.close()

        assert total == 2
        assert set(started) == {"first", "second"}

    def test_consumes_staged_provider_results(self, monkeypatch):
        future = Future()
        future.set_result([
            {
                "id": "child",
                "subscription_id": "sub-1",
                "resource_group": "rg-data",
                "name": "child",
                "type": "Microsoft.Storage/storageAccounts/blobServices/containers",
                "location": "westus",
                "sku": "blob",
                "tags": json.dumps({}),
                "is_public": 0,
                "is_restricted": 0,
                "ip_restrictions": json.dumps([]),
                "endpoints": json.dumps([]),
                "auth_methods": json.dumps([]),
                "fqdn": None,
                "pipeline_tag": None,
                "raw_json": json.dumps({"name": "child"}),
            }
        ])

        def fake_staged_provider(subscription_id, progress=None, stage_backfill=False):
            return StagedRows(
                core_rows=[{
                    "id": "core",
                    "subscription_id": subscription_id,
                    "resource_group": "rg-data",
                    "name": "core",
                    "type": "Microsoft.Storage/storageAccounts",
                    "location": "westus",
                    "sku": "Standard_LRS",
                    "tags": json.dumps({}),
                    "is_public": 1,
                    "is_restricted": 0,
                    "ip_restrictions": json.dumps([]),
                    "endpoints": json.dumps([]),
                    "auth_methods": json.dumps([]),
                    "fqdn": None,
                    "pipeline_tag": None,
                    "raw_json": json.dumps({"name": "core"}),
                }],
                backfill_jobs=[BackfillJob(label="core", future=future)],
            )

        monkeypatch.setattr(
            harvest_azure_assets,
            "PROVIDERS",
            [("Storage", fake_staged_provider)],
        )
        monkeypatch.setattr(harvest_azure_assets.appgw_routing_map, "harvest_routing", lambda *args, **kwargs: (0, 0, 0, 0))
        monkeypatch.setattr(harvest_azure_assets.aks, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.apim, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.apim_routing_map, "harvest_backends", lambda *args, **kwargs: (0, 0))
        monkeypatch.setattr(harvest_azure_assets.function_apps, "harvest_http_triggers", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.front_door, "harvest_routes", lambda *args, **kwargs: 0)
        monkeypatch.setattr(harvest_azure_assets.firewall, "harvest_rules", lambda *args, **kwargs: (0, 0))

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets._ensure_schema(conn)
            total = harvest_azure_assets.harvest_subscription({"id": "sub-1", "name": "sub"}, conn, dry_run=False)
            rows = conn.execute("SELECT id FROM provisioned_assets ORDER BY id").fetchall()
        finally:
            conn.close()

        assert total == 2
        assert rows == [("child",), ("core",)]


class TestHarvestProgressRendering:
    def test_renders_a_progress_bar_for_each_resource_type(self, monkeypatch, capsys):
        monkeypatch.setattr(harvest_azure_assets.time, "monotonic", lambda: 100.0)

        progress = harvest_azure_assets.HarvestProgress(["App Gateways", "APIM"])
        progress.mark_running("App Gateways")
        progress.render(force=True)

        out = capsys.readouterr().out
        assert "[harvest] provider progress 0/2" in out
        assert re.search(r"App Gateways\s+\[[=>\-]{24}\] running 00:00 — fetching inventory", out), out
        assert re.search(r"APIM\s+\[-{24}\] queued", out), out

class TestAppGatewayRewriteHarvest:
    def test_persists_rewrite_rule_sets(self):
        conn = sqlite3.connect(":memory:")
        try:
            appgw_routing_map._ensure_appgw_schema(conn)

            gw_stub = {
                "name": "gw-one",
                "resourceGroup": "rg-net",
                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one",
            }
            gw = {
                "id": gw_stub["id"],
                "name": "gw-one",
                "resourceGroup": "rg-net",
                "properties": {
                    "requestRoutingRules": [{
                        "name": "basic-rule",
                        "properties": {
                            "ruleType": "Basic",
                            "httpListener": {
                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/httpListeners/listener-one",
                            },
                            "rewriteRuleSet": {
                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/rewriteRuleSets/rewrite-one",
                            },
                        },
                    }],
                    "rewriteRuleSets": [{
                        "name": "rewrite-one",
                        "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/rewriteRuleSets/rewrite-one",
                        "properties": {
                            "provisioningState": "Succeeded",
                            "rewriteRules": [{
                                "name": "add-header",
                                "ruleSequence": 10,
                                "conditions": [{"variable": "http_req_Authorization", "pattern": "^Bearer"}],
                                "actionSet": {
                                    "requestHeaderConfigurations": [{"headerName": "X-Test", "headerValue": "1"}],
                                    "responseHeaderConfigurations": [{"headerName": "Strict-Transport-Security", "headerValue": "max-age=1"}],
                                    "urlConfiguration": {"modifiedPath": "/abc", "reroute": False},
                                },
                            }],
                        },
                    }],
                },
            }

            set_count, rule_count = appgw_routing_map.process_rewrite_rule_sets(
                gw_stub, "sub-1", conn, dry_run=False, now="2026-06-01T00:00:00Z", gw=gw
            )
            row = conn.execute(
                "SELECT set_name, attached_route_count, rewrite_rule_count, attached_routes, rewrite_rules "
                "FROM appgw_rewrite_rule_sets"
            ).fetchone()
        finally:
            conn.close()

        assert set_count == 1
        assert rule_count == 1
        assert row[0] == "rewrite-one"
        assert row[1] == 1
        assert row[2] == 1
        attached_routes = json.loads(row[3])
        rewrite_rules = json.loads(row[4])
        assert attached_routes[0]["routing_rule_name"] == "basic-rule"
        assert rewrite_rules[0]["name"] == "add-header"

    def test_apim_backend_derivation_stays_cli_free(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets._ensure_schema(conn)
            apim_routing_map._ensure_apim_schema(conn)
            conn.execute(
                """
                INSERT INTO apim_api_routes (
                    id, subscription_id, apim_name, apim_resource_id,
                    api_name, api_display_name, api_path, api_protocols,
                    backend_id, backend_url, service_url, requires_subscription,
                    last_synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "orders-apim::orders-api",
                    "sub-1",
                    "orders-apim",
                    "apim-id",
                    "orders-api",
                    "Orders API",
                    "orders",
                    '["https"]',
                    None,
                    "https://backend.example.com/v1",
                    "https://backend.example.com/v1",
                    1,
                    "2026-06-01T00:00:00Z",
                ),
            )

            def _fail(*args, **kwargs):
                raise AssertionError("APIM CLI should not be called during backend derivation")

            monkeypatch.setattr(apim_routing_map, "list_apim_instances", _fail)
            monkeypatch.setattr(apim_routing_map, "list_backends", _fail)

            backend_count, route_count = apim_routing_map.harvest_backends("sub-1", conn, dry_run=False)
            row = conn.execute(
                "SELECT id, apim_name, backend_id, url, protocol FROM apim_backends"
            ).fetchone()
        finally:
            conn.close()

        assert backend_count == 1
        assert route_count == 1
        assert row[0] == "orders-apim::backend.example.com/v1"
        assert row[1] == "orders-apim"
        assert row[2] == "backend.example.com/v1"
        assert row[3] == "https://backend.example.com/v1"
        assert row[4] == "https"

    def test_invokes_apim_backend_links_harvest(self, monkeypatch):
        calls = []

        def fake_harvest_backends(subscription_id, conn, dry_run=False):
            calls.append((subscription_id, dry_run))
            return 0, 0

        monkeypatch.setattr(harvest_azure_assets, "PROVIDERS", [])
        monkeypatch.setattr(harvest_azure_assets.apim_routing_map, "harvest_backends", fake_harvest_backends)

        conn = sqlite3.connect(":memory:")
        try:
            harvest_azure_assets.harvest_subscription({"id": "sub-1", "name": "sub"}, conn, dry_run=True)
        finally:
            conn.close()

        assert calls == [("sub-1", True)]


class TestAppConfigurationHarvest:
    def test_does_not_call_role_assignment_lookup(self, monkeypatch):
        store = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.AppConfiguration/configurationStores/store-1",
            "name": "store-1",
            "type": "Microsoft.AppConfiguration/configurationStores",
            "resourceGroup": "rg-1",
            "location": "westeurope",
            "sku": {"name": "Standard"},
            "tags": {},
            "properties": {
                "endpoint": "https://store-1.azconfig.io/",
                "publicNetworkAccess": "Enabled",
                "disableLocalAuth": False,
                "networkAcls": {"defaultAction": "Allow"},
                "privateEndpointConnections": [],
            },
        }

        calls = []

        def fake_az(args, subscription_id):
            calls.append(args)
            if args[:3] == ["role", "assignment", "list"]:
                raise AssertionError("App Configuration harvest should not query role assignments")
            if args == ["appconfig", "list"]:
                return [store]
            return []

        def fake_build_endpoints(entries, timeout=5):
            return json.dumps([
                {"address": address, "port": port, "protocol": protocol}
                for address, port, protocol in entries
            ])

        monkeypatch.setattr(app_configuration, "az", fake_az)
        monkeypatch.setattr(app_configuration, "build_endpoints", fake_build_endpoints)

        results = app_configuration.harvest("sub-1")
        assert len(results) == 1
        extra = json.loads(results[0]["raw_json"])["_extra"]
        assert extra["rbac_check"]["role_assignment_lookup"] == "skipped"
        assert all(call[:3] != ["role", "assignment", "list"] for call in calls)


class TestRefreshPublicFlags:
    def test_does_not_override_network_restrictions(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                type TEXT,
                is_public INTEGER,
                is_restricted INTEGER,
                raw_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (id, subscription_id, type, is_public, is_restricted, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "storage-1",
                "sub-1",
                "Microsoft.Storage/storageAccounts",
                0,
                1,
                '{"properties":{"allowBlobPublicAccess":true}}',
            ),
        )
        try:
            correlate_assets.refresh_public_flags(conn, "sub-1")
            row = conn.execute(
                "SELECT is_public, is_restricted FROM provisioned_assets WHERE id = ?",
                ("storage-1",),
            ).fetchone()
        finally:
            conn.close()

        assert row == (0, 1)


class TestFrontDoorClassicRoutes:
    def test_extracts_frontend_to_backend_routes(self):
        profile = {
            "name": "fd-classic",
            "properties": {
                "frontendEndpoints": [
                    {"id": "/frontends/fe1", "name": "fe1", "properties": {"hostName": "www.contoso.com", "webApplicationFirewallPolicyLink": {"id": "/waf/policy1"}}},
                ],
                "backendPools": [
                    {"id": "/backendPools/pool1", "name": "pool1", "properties": {"backends": [{"address": "origin.contoso.internal", "httpPort": 80, "httpsPort": 443}]}}
                ],
                "routingRules": [
                    {"name": "route1", "properties": {"frontendEndpoints": [{"id": "/frontends/fe1"}], "backendPool": {"id": "/backendPools/pool1"}, "acceptedProtocols": ["Https"], "patternsToMatch": ["/api/*"], "httpsRedirect": True}},
                ],
            },
        }
        routes = _extract_classic_routes(profile)
        assert routes == [{
            "profile_name": "fd-classic",
            "profile_tier": "Classic",
            "endpoint_name": "fe1",
            "hostname": "www.contoso.com",
            "route_name": "route1",
            "patterns": ["/api/*"],
            "origin_group": "pool1",
            "origins": ["origin.contoso.internal"],
            "waf_policy": "policy1",
            "https_redirect": 1,
            "exposure_level": "Public",
        }]


class TestFrontDoorAfdRoutes:
    def test_extracts_patterns_origin_group_and_https_redirect(self):
        route = {
            "name": "afd-route",
            "properties": {
                "patternsToMatch": ["/images/*", "/css/*"],
                "originGroup": {"id": "/profiles/p1/originGroups/group1"},
                "httpsRedirect": "Enabled",
            },
        }
        extracted = _extract_afd_route(
            route,
            hostname="site.azurefd.net",
            profile_name="afd-profile",
            profile_tier="Standard_AzureFrontDoor",
            endpoint_name="ep1",
            origins=["origin1.contoso.com"],
            waf_policy=None,
        )
        assert extracted == {
            "profile_name": "afd-profile",
            "profile_tier": "Standard_AzureFrontDoor",
            "endpoint_name": "ep1",
            "hostname": "site.azurefd.net",
            "route_name": "afd-route",
            "patterns": ["/images/*", "/css/*"],
            "origin_group": "group1",
            "origins": ["origin1.contoso.com"],
            "waf_policy": None,
            "https_redirect": 1,
            "exposure_level": "Public",
        }


class TestAppGatewayRouteExposure:
    def _make_gateway(self, *, public: bool) -> dict:
        frontend_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/frontendIPConfigurations/fe-one"
        frontend_port_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/frontendPorts/port-one"
        listener_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/httpListeners/listener-one"
        pool_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/backendAddressPools/pool-one"
        settings_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one/backendHttpSettingsCollection/http-one"

        frontend_props: dict[str, object] = {}
        if public:
            frontend_props["publicIPAddress"] = {"id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/publicIPAddresses/pip-one"}

        return {
            "name": "gw-one",
            "resourceGroup": "rg-net",
            "properties": {
                "frontendIPConfigurations": [{
                    "name": "fe-one",
                    "id": frontend_id,
                    "properties": frontend_props,
                }],
                "frontendPorts": [{
                    "name": "port-one",
                    "id": frontend_port_id,
                    "properties": {"port": 443},
                }],
                "httpListeners": [{
                    "name": "listener-one",
                    "id": listener_id,
                    "properties": {
                        "protocol": "Https",
                        "hostName": "api.example.com",
                        "frontendIPConfiguration": {"id": frontend_id},
                        "frontendPort": {"id": frontend_port_id},
                    },
                }],
                "backendAddressPools": [{
                    "name": "pool-one",
                    "id": pool_id,
                    "properties": {"backendAddresses": [{"fqdn": "backend.example.local"}]},
                }],
                "backendHttpSettingsCollection": [{
                    "name": "http-one",
                    "id": settings_id,
                    "properties": {"port": 443, "protocol": "Https"},
                }],
                "requestRoutingRules": [{
                    "name": "rule-one",
                    "properties": {
                        "ruleType": "Basic",
                        "httpListener": {"id": listener_id},
                        "backendAddressPool": {"id": pool_id},
                        "backendHttpSettings": {"id": settings_id},
                    },
                }],
            },
        }

    def test_public_frontend_marks_route_public(self):
        routes = appgw_routing_map.extract_routes(self._make_gateway(public=True), "sub-1")
        assert len(routes) == 1
        assert routes[0]["exposure_level"] == "Public"

    def test_private_frontend_marks_route_internal(self):
        routes = appgw_routing_map.extract_routes(self._make_gateway(public=False), "sub-1")
        assert len(routes) == 1
        assert routes[0]["exposure_level"] == "Internal"


# ---------------------------------------------------------------------------
# subscription_assets_from_rows — new columns (is_restricted, waf_mode)
# ---------------------------------------------------------------------------

class TestSubscriptionAssetsFromRows:
    """subscription_diagram_helpers.subscription_assets_from_rows parses new columns."""

    def _make_row(self, *, is_restricted=0, waf_mode=None):
        # Row layout: name, type, rg, fqdn, is_public, sku, id, has_waf, listeners,
        #             is_restricted, waf_mode
        return (
            "my-appgw", "Microsoft.Network/applicationGateways", "rg1",
            "appgw.azurefd.net", 1, "WAF_v2", "/subs/s1/appgw",
            1, "HTTPS:443",
            is_restricted, waf_mode,
        )

    def _import_helper(self):
        import importlib.util, sys
        web_dir = ROOT / "web"
        spec = importlib.util.spec_from_file_location(
            "subscription_diagram_helpers",
            web_dir / "subscription_diagram_helpers.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_is_restricted_false_when_zero(self):
        mod = self._import_helper()
        row = self._make_row(is_restricted=0)
        assets = mod.subscription_assets_from_rows([row], lambda t: t)
        assert assets[0]["is_restricted"] is False

    def test_is_restricted_true_when_one(self):
        mod = self._import_helper()
        row = self._make_row(is_restricted=1)
        assets = mod.subscription_assets_from_rows([row], lambda t: t)
        assert assets[0]["is_restricted"] is True

    def test_waf_mode_none_when_absent(self):
        mod = self._import_helper()
        row = self._make_row(waf_mode=None)
        assets = mod.subscription_assets_from_rows([row], lambda t: t)
        assert assets[0]["waf_mode"] is None

    def test_waf_mode_stored_correctly(self):
        mod = self._import_helper()
        row = self._make_row(waf_mode="Prevention")
        assets = mod.subscription_assets_from_rows([row], lambda t: t)
        assert assets[0]["waf_mode"] == "Prevention"

    def test_short_row_defaults_is_restricted_false(self):
        """Rows from older DBs without the new column default to False."""
        mod = self._import_helper()
        # Only 9 columns (no is_restricted, no waf_mode)
        row = (
            "gw", "Microsoft.Network/applicationGateways", "rg", "gw.azurefd.net",
            1, "WAF_v2", "/id", True, "HTTPS:443",
        )
        assets = mod.subscription_assets_from_rows([row], lambda t: t)
        assert assets[0]["is_restricted"] is False
        assert assets[0]["waf_mode"] is None

    def test_vmss_exposes_vnet_and_subnet_from_extra_metadata(self):
        mod = self._import_helper()
        vmss_row = (
            "vmss-one",
            "Microsoft.Compute/virtualMachineScaleSets",
            "rg-compute",
            "",
            0,
            "Standard_B2s",
            "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Compute/virtualMachineScaleSets/vmss-one",
            0,
            None,
            0,
            None,
            None,
            json.dumps({
                "properties": {},
                "_extra": {
                    "subnet_id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app",
                    "subnet_name": "app",
                    "vnet_name": "vnet-one",
                    "vnet_resource_group": "rg-net",
                    "subnet_ids": [
                        "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app",
                    ],
                },
            }),
            None,
        )

        assets = mod.subscription_assets_from_rows([vmss_row], lambda t: t)

        assert assets[0]["subnet_id"].endswith("/subnets/app")
        assert assets[0]["subnet_name"] == "app"
        assert assets[0]["vnet_name"] == "vnet-one"
        assert assets[0]["parent_vnet_name"] == "vnet-one"
        assert assets[0]["parent_vnet_resource_group"] == "rg-net"

    def test_slots_pick_up_parent_metadata_from_extra(self):
        mod = self._import_helper()
        slot_row = (
            "functions_windows-staging",
            "Microsoft.Web/sites/slots",
            "rg-app",
            "functions-staging.azurewebsites.net",
            1,
            "Y1",
            "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/functions_windows/slots/staging",
            0,
            None,
            0,
            None,
            None,
            json.dumps({
                "kind": "functionapp,linux",
                "_extra": {
                    "slot_parent": "functions_windows",
                    "slot_name": "staging",
                },
            }),
            None,
        )

        assets = mod.subscription_assets_from_rows([slot_row], lambda t: t)

        assert assets[0]["parent_name"] == "functions_windows"
        assert assets[0]["parent_resource_group"] == "rg-app"
        assert assets[0]["parent_type_label"] == "Function App"


class TestSubscriptionOverlayPlanLinks:
    """App Service Plans should drill into hosted apps without top-level hosting arrows."""

    def _import_helper(self):
        import importlib.util
        web_dir = ROOT / "web"
        spec = importlib.util.spec_from_file_location(
            "subscription_diagram_helpers",
            web_dir / "subscription_diagram_helpers.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_hosted_apps_fold_into_plan_node(self):
        mod = self._import_helper()
        rows = [
            (
                "ingress", "Microsoft.Network/applicationGateways", "rg1",
                "ingress.azurefd.net", 1, "WAF_v2", "/subs/s1/ingress",
                1, "HTTPS:443", 0, None,
            ),
            (
                "functions_windows", "Microsoft.Web/sites", "rg1",
                "functions.azurewebsites.net", 0, "F1", "/subs/s1/functions",
                0, None, 0, None,
            ),
            (
                "functions_plan", "Microsoft.Web/serverfarms", "rg1",
                "", 0, "S1", "/subs/s1/functions-plan",
                0, None, 0, None,
            ),
        ]
        plan_links = [("rg1", "functions_windows", "rg1", "functions_plan")]
        diagrams = mod.build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
            rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
            friendly_type=lambda t: t,
            get_icon_path=lambda t: None,
            normalize_attack_paths=lambda *args, **kwargs: [],
            plan_links=plan_links,
        )
        view = diagrams[0]["views"]["connectivity"]
        assert "hosted on" not in view["mermaid"]
        assert diagrams[0]["asset_summary"]["backends"] == 1
        titles = {v.get("title") for v in view["node_drilldown_map"].values()}
        assert "functions_plan" in titles, titles
        assert "functions_windows" not in titles, titles


class TestSubscriptionNetworkRendering:
    def _import_helper(self):
        import importlib.util
        web_dir = ROOT / "web"
        spec = importlib.util.spec_from_file_location(
            "subscription_diagram_helpers",
            web_dir / "subscription_diagram_helpers.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_synthesizes_subnet_nodes_and_edges_from_vnet_raw_json(self):
        mod = self._import_helper()
        vnet_raw = json.dumps({
            "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one",
            "name": "vnet-one",
            "properties": {
                "subnets": [{
                    "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app",
                    "name": "app",
                    "properties": {
                        "addressPrefix": "10.0.1.0/24",
                        "networkSecurityGroup": {"id": "/nsgs/app-nsg", "name": "app-nsg"},
                        "routeTable": {"id": "/routetables/app-rt", "name": "app-rt"},
                    },
                }],
            },
            "_extra": {
                "subnets": [{
                    "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app",
                    "name": "app",
                    "properties": {
                        "addressPrefix": "10.0.1.0/24",
                        "networkSecurityGroup": {"id": "/nsgs/app-nsg", "name": "app-nsg"},
                        "routeTable": {"id": "/routetables/app-rt", "name": "app-rt"},
                    },
                }],
            },
        })
        pe_raw = json.dumps({
            "properties": {
                "subnet": {
                    "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app"
                }
            },
            "_extra": {
                "subnet_id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one/subnets/app"
            },
        })
        rows = [
            ("vnet-one", "Microsoft.Network/virtualNetworks", "rg-net", "", 0, None, "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-one", 0, None, 0, None, None, vnet_raw, "[]"),
            ("pe-one", "Microsoft.Network/privateEndpoints", "rg-net", "", 0, None, "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/privateEndpoints/pe-one", 0, None, 0, None, None, pe_raw, "[]"),
        ]

        diagrams = mod.build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
            rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
            friendly_type=lambda t: "Subnet" if "subnets" in (t or "").lower() else t,
            get_icon_path=lambda t: None,
            normalize_attack_paths=lambda *args, **kwargs: [],
        )

        mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
        assert "contains" in mermaid
        assert "in subnet" in mermaid
        assert "app-nsg" in mermaid


class TestKeyVaultHarvest:
    def test_classifies_ip_allowlisted_vault_as_restricted(self, monkeypatch):
        vault_id = "/subscriptions/sub-1/resourceGroups/rg-sec/providers/Microsoft.KeyVault/vaults/kv-one"
        vault = {
            "id": vault_id,
            "name": "kv-one",
            "resourceGroup": "rg-sec",
            "location": "westus",
            "type": "Microsoft.KeyVault/vaults",
            "properties": {
                "vaultUri": "https://kv-one.vault.azure.net/",
                "publicNetworkAccess": "Enabled",
                "networkAcls": {
                    "defaultAction": "Deny",
                    "ipRules": [
                        {"value": "51.132.44.20/32"},
                        {"value": "51.137.137.41/32"},
                    ],
                    "virtualNetworkRules": [
                        {
                            "virtualNetworkResourceId": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prodgreen",
                        }
                    ],
                    "bypass": "AzureServices",
                },
            },
        }

        def fake_az(args, subscription_id):
            if args == ["keyvault", "list"]:
                return [vault]
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(key_vault, "az", fake_az)
        monkeypatch.setattr(
            key_vault,
            "build_endpoints",
            lambda entries, timeout=5: json.dumps([
                {"address": address, "port": port, "protocol": protocol}
                for address, port, protocol in entries
            ]),
        )

        rows = key_vault.harvest("sub-1")
        assert len(rows) == 1

        row = rows[0]
        assert row["id"] == vault_id
        assert row["is_public"] == 0
        assert row["is_restricted"] == 1
        assert json.loads(row["ip_restrictions"]) == [
            "51.132.44.20/32",
            "51.137.137.41/32",
            "vnet:prodgreen",
        ]

        extra = json.loads(row["raw_json"])["_extra"]
        assert extra["public_network_access"] == "Enabled"
        assert extra["network_default_action"] == "Deny"
        assert extra["network_access_mode"] == "ip_restricted"
        assert extra["ip_rule_count"] == 2
        assert extra["virtual_network_rule_count"] == 1
        assert extra["ip_restriction_count"] == 3


class TestSqlServerHarvest:
    def test_classifies_firewalled_server_as_restricted(self, monkeypatch):
        server = {
            "id": "/subscriptions/sub-1/resourceGroups/cbuk-core-prodgreen-sql-uksouth/providers/Microsoft.Sql/servers/cbuk-core-prodgreen-sql-uksouth",
            "name": "cbuk-core-prodgreen-sql-uksouth",
            "resourceGroup": "cbuk-core-prodgreen-sql-uksouth",
            "location": "uksouth",
            "type": "Microsoft.Sql/servers",
            "properties": {
                "publicNetworkAccess": "Enabled",
                "fullyQualifiedDomainName": "cbuk-core-prodgreen-sql-uksouth.database.windows.net",
            },
        }
        firewall_rules = [
            {
                "name": "hub_mgmt_zpa_uks",
                "startIpAddress": "40.81.155.226",
                "endIpAddress": "40.81.155.226",
            },
            {
                "name": "internalprod_firewall_uks",
                "startIpAddress": "4.234.150.176",
                "endIpAddress": "4.234.150.183",
            },
        ]

        def fake_az(args, subscription_id):
            if args == ["sql", "server", "list"]:
                return [server]
            if args == ["sql", "server", "firewall-rule", "list", "-s", "cbuk-core-prodgreen-sql-uksouth", "-g", "cbuk-core-prodgreen-sql-uksouth"]:
                return firewall_rules
            if args == ["sql", "server", "ad-admin", "list", "--server", "cbuk-core-prodgreen-sql-uksouth", "--resource-group", "cbuk-core-prodgreen-sql-uksouth"]:
                return []
            if args == ["sql", "db", "list", "--server", "cbuk-core-prodgreen-sql-uksouth", "--resource-group", "cbuk-core-prodgreen-sql-uksouth"]:
                return []
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(sql_server, "az", fake_az)
        monkeypatch.setattr(sql_server, "build_endpoints", lambda entries, timeout=5: "[]")

        rows = sql_server.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "cbuk-core-prodgreen-sql-uksouth"
        assert row["is_public"] == 0
        assert row["is_restricted"] == 1
        assert json.loads(row["ip_restrictions"]) == [
            "40.81.155.226-40.81.155.226",
            "4.234.150.176-4.234.150.183",
        ]


class TestServiceBusHarvest:
    def test_classifies_public_service_bus_as_public(self, monkeypatch):
        ns = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.ServiceBus/namespaces/sb-public",
            "name": "sb-public",
            "resourceGroup": "rg-msg",
            "location": "westus",
            "type": "Microsoft.ServiceBus/namespaces",
            "sku": {"name": "Standard", "tier": "Standard"},
            "properties": {
                "serviceBusEndpoint": "https://sb-public.servicebus.windows.net:443/",
                "status": "Active",
                "publicNetworkAccess": "Enabled",
            },
        }

        def fake_az(args, subscription_id):
            if args == ["servicebus", "namespace", "list"]:
                return [ns]
            if args[:3] == ["servicebus", "namespace", "network-rule-set"]:
                return []
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(service_bus, "az", fake_az)
        monkeypatch.setattr(
            service_bus,
            "build_endpoints",
            lambda entries, timeout=5: json.dumps([
                {"address": address, "port": port, "protocol": protocol}
                for address, port, protocol in entries
            ]),
        )

        rows = service_bus.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "sb-public"
        assert row["is_public"] == 1
        assert row["is_restricted"] == 0
        assert json.loads(row["ip_restrictions"]) == []

    def test_classifies_restricted_service_bus_as_restricted(self, monkeypatch):
        ns = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.ServiceBus/namespaces/sb-restricted",
            "name": "sb-restricted",
            "resourceGroup": "rg-msg",
            "location": "westus",
            "type": "Microsoft.ServiceBus/namespaces",
            "sku": {"name": "Standard", "tier": "Standard"},
            "properties": {
                "serviceBusEndpoint": "https://sb-restricted.servicebus.windows.net:443/",
                "status": "Active",
                "publicNetworkAccess": "Enabled",
            },
        }

        network_rules = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.ServiceBus/namespaces/sb-restricted/networkRuleSets/default",
            "name": "default",
            "type": "Microsoft.ServiceBus/namespaces/networkRuleSets",
            "properties": {
                "defaultAction": "Deny",
                "ipRules": [
                    {"value": "192.168.1.0/24", "action": "Allow"},
                ],
                "virtualNetworkRules": [
                    {"id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod/subnets/default"}
                ],
            },
        }

        def fake_az(args, subscription_id):
            if args == ["servicebus", "namespace", "list"]:
                return [ns]
            if args[:5] == ["servicebus", "namespace", "network-rule-set", "show", "--namespace-name"]:
                return [network_rules]
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(service_bus, "az", fake_az)
        monkeypatch.setattr(
            service_bus,
            "build_endpoints",
            lambda entries, timeout=5: json.dumps([
                {"address": address, "port": port, "protocol": protocol}
                for address, port, protocol in entries
            ]),
        )

        rows = service_bus.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "sb-restricted"
        assert row["is_public"] == 0
        assert row["is_restricted"] == 1
        restrictions = json.loads(row["ip_restrictions"])
        assert "192.168.1.0/24" in restrictions
        assert any("vnet:" in r for r in restrictions)


class TestEventHubHarvest:
    def test_classifies_restricted_event_hub_as_restricted(self, monkeypatch):
        ns = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.EventHub/namespaces/eh-restricted",
            "name": "eh-restricted",
            "resourceGroup": "rg-msg",
            "location": "westus",
            "type": "Microsoft.EventHub/namespaces",
            "sku": {"name": "Standard", "tier": "Standard", "capacity": 1},
            "properties": {
                "serviceBusEndpoint": "https://eh-restricted.servicebus.windows.net:443/",
                "isAutoInflateEnabled": False,
                "publicNetworkAccess": "Enabled",
            },
        }

        network_rules = {
            "id": "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.EventHub/namespaces/eh-restricted/networkRuleSets/default",
            "name": "default",
            "type": "Microsoft.EventHub/namespaces/networkRuleSets",
            "properties": {
                "defaultAction": "Deny",
                "ipRules": [
                    {"value": "10.0.0.0/8", "action": "Allow"},
                ],
                "virtualNetworkRules": [],
            },
        }

        def fake_az(args, subscription_id):
            if args == ["eventhubs", "namespace", "list"]:
                return [ns]
            if args[:5] == ["eventhubs", "namespace", "network-rule-set", "show", "--namespace-name"]:
                return [network_rules]
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(event_hub, "az", fake_az)
        monkeypatch.setattr(
            event_hub,
            "build_endpoints",
            lambda entries, timeout=5: json.dumps([
                {"address": address, "port": port, "protocol": protocol}
                for address, port, protocol in entries
            ]),
        )

        rows = event_hub.harvest("sub-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "eh-restricted"
        assert row["is_public"] == 0
        assert row["is_restricted"] == 1
        assert json.loads(row["ip_restrictions"]) == ["10.0.0.0/8"]
