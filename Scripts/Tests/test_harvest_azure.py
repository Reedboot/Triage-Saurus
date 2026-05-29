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
