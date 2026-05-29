#!/usr/bin/env python3
"""Unit tests for Scripts/Harvest/Azure helpers and correlate_assets logic.

These tests use only pure Python (no az CLI, no DB) by exercising functions
that are isolated from external dependencies.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Harvest"))
sys.path.insert(0, str(ROOT / "Scripts" / "Harvest" / "Azure"))
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

import pytest
from Azure._helpers import safe_str, infer_fqdn, build_endpoints, extract_ip_restrictions, infer_sku


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
