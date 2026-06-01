#!/usr/bin/env python3
"""Unit tests for Scripts/Harvest/Azure helpers and correlate_assets logic.

These tests use only pure Python (no az CLI, no DB) by exercising functions
that are isolated from external dependencies.
"""
import json
import sys
import sqlite3
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
from Azure._helpers import safe_str, infer_fqdn, build_endpoints, extract_ip_restrictions, infer_sku
from Azure import app_configuration, storage


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
from Azure.apim import _get_gateway_hosts, _get_apim_exposure_level
from Azure.function_apps import _extract_http_triggers
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

    def _make_ingress(self, host, path, svc_name):
        return {
            "metadata": {"namespace": "default", "name": "ing1"},
            "spec": {"rules": [{
                "host": host,
                "http": {"paths": [{"path": path, "backend": {"service": {"name": svc_name, "port": {"number": 80}}}}]},
            }]},
        }

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

    def test_internal_without_public_ip(self):
        firewall = {"properties": {"ipConfigurations": [{"properties": {}}]}}
        assert _get_firewall_exposure_level(firewall) == "Internal"


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
        view = mod.build_subscription_overlay_views(
            rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace("-", "_"),
            friendly_type=lambda t: t,
            get_icon_path=lambda t: None,
            normalize_attack_paths=lambda *args, **kwargs: [],
            plan_links=plan_links,
        )
        assert "hosted on" not in view["exposure"]["mermaid"]
        assert view["exposure"]["asset_summary"]["backends"] == 1
        titles = {v.get("title") for v in view["exposure"]["node_drilldown_map"].values()}
        assert "functions_plan" in titles, titles
        assert "functions_windows" not in titles, titles
