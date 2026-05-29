#!/usr/bin/env python3
"""Unit tests for Scripts/Enrich/enrich_findings.py pure functions.

Tests only the deterministic logic (no LLM calls, no DB).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Enrich"))
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

import pytest
from enrich_findings import _is_credential_finding, _cloud_posture_context_block, _build_prompt


# ---------------------------------------------------------------------------
# _is_credential_finding
# ---------------------------------------------------------------------------

class TestIsCredentialFinding:
    def test_detects_password_in_rule_id(self):
        assert _is_credential_finding({"rule_id": "hardcoded-password"}) is True

    def test_detects_secret_in_title(self):
        assert _is_credential_finding({"rule_id": None, "title": "Exposed API secret"}) is True

    def test_detects_token_in_reason(self):
        assert _is_credential_finding({"rule_id": None, "title": None, "reason": "Token in source"}) is True

    def test_returns_false_for_unrelated_finding(self):
        assert _is_credential_finding({"rule_id": "sql-injection", "title": "SQL Injection", "reason": "Unsanitised input"}) is False

    def test_returns_false_for_empty_finding(self):
        assert _is_credential_finding({}) is False

    def test_case_insensitive(self):
        assert _is_credential_finding({"rule_id": "HARDCODED-SECRET"}) is True

    def test_detects_connection_string(self):
        assert _is_credential_finding({"title": "ConnectionString exposed"}) is True

    def test_detects_private_key(self):
        assert _is_credential_finding({"rule_id": "private_key-in-code"}) is True


# ---------------------------------------------------------------------------
# _cloud_posture_context_block
# ---------------------------------------------------------------------------

class TestCloudPostureContextBlock:
    def test_returns_empty_for_none(self):
        assert _cloud_posture_context_block(None) == ""

    def test_returns_empty_for_empty_dict(self):
        assert _cloud_posture_context_block({}) == ""

    def test_waf_protected_mention(self):
        posture = {
            "behind_waf": True,
            "behind_app_gateway": True,
            "app_gateway_name": "prod-gw",
        }
        result = _cloud_posture_context_block(posture)
        assert "WAF-protected" in result
        assert "prod-gw" in result

    def test_no_waf_direct_exposure(self):
        posture = {"behind_waf": False, "behind_app_gateway": False}
        result = _cloud_posture_context_block(posture)
        assert "direct internet exposure" in result.lower() or "no App Gateway" in result

    def test_apim_present(self):
        posture = {"behind_apim": True, "apim_name": "my-apim"}
        result = _cloud_posture_context_block(posture)
        assert "my-apim" in result

    def test_apim_absent(self):
        posture = {"behind_apim": False}
        result = _cloud_posture_context_block(posture)
        assert "APIM routing: NO" in result

    def test_public_exposure_label(self):
        posture = {"endpoint_exposure": "public"}
        result = _cloud_posture_context_block(posture)
        assert "FULLY PUBLIC" in result

    def test_private_exposure_label(self):
        posture = {"endpoint_exposure": "private"}
        result = _cloud_posture_context_block(posture)
        assert "PRIVATE" in result

    def test_restricted_exposure_label(self):
        posture = {"endpoint_exposure": "restricted"}
        result = _cloud_posture_context_block(posture)
        assert "IP-RESTRICTED" in result

    def test_auth_methods_included(self):
        posture = {"auth_methods": ["oauth2", "api-key"]}
        result = _cloud_posture_context_block(posture)
        assert "oauth2" in result
        assert "api-key" in result

    def test_aks_cluster_secured(self):
        posture = {"aks_cluster": "prod-aks", "aks_secured": True}
        result = _cloud_posture_context_block(posture)
        assert "prod-aks" in result
        assert "IP-restricted" in result or "authorizedIPRanges" in result

    def test_aks_cluster_unsecured(self):
        posture = {"aks_cluster": "dev-aks", "aks_secured": False}
        result = _cloud_posture_context_block(posture)
        assert "publicly accessible" in result


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_returns_non_empty_string(self):
        row = {"rule_id": "sql-injection", "title": "SQL Injection", "code_snippet": "query = f'SELECT * FROM users WHERE id={user_id}'"}
        result = _build_prompt(row)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_includes_rule_id_in_prompt(self):
        row = {"rule_id": "my-test-rule"}
        result = _build_prompt(row)
        assert "my-test-rule" in result

    def test_includes_credential_guidance_for_cred_finding(self):
        row = {"rule_id": "hardcoded-password", "code_snippet": "pass = 'hunter2'"}
        result = _build_prompt(row)
        assert "credential" in result.lower() or "real_credential" in result

    def test_includes_cloud_posture_block_when_provided(self):
        row = {"rule_id": "test-rule"}
        posture = {"endpoint_exposure": "public"}
        result = _build_prompt(row, cloud_posture=posture)
        assert "FULLY PUBLIC" in result

    def test_no_cloud_posture_when_none(self):
        row = {"rule_id": "test-rule"}
        result = _build_prompt(row, cloud_posture=None)
        # Should still produce a valid prompt
        assert isinstance(result, str)
        assert len(result) > 10
