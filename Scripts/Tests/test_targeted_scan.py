#!/usr/bin/env python3
"""Regression tests for scan routing mappings."""

from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import targeted_scan


def test_new_azure_alias_rules_route_to_expected_folders():
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-azure-function-app"] == ["Azure/AppService"]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-azure-virtual-machine"] == ["Azure/VM", "Azure/Compute"]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-azure-cosmosdb-account"] == ["Azure/CosmosDB"]


def test_tencentcloud_detection_rules_are_mapped():
    expected_ids = {
        "context-tencentcloud-cvm-instance",
        "context-tencentcloud-tke-cluster",
        "context-tencentcloud-tke-node-pool",
        "context-tencentcloud-cos-bucket",
        "context-tencentcloud-mysql-instance",
        "context-tencentcloud-postgresql-instance",
        "context-tencentcloud-kms-key",
        "context-tencentcloud-vpc",
        "context-tencentcloud-subnet",
        "context-tencentcloud-security-group",
        "context-tencentcloud-security-group-rule",
        "context-tencentcloud-clb-instance",
        "context-tencentcloud-apigateway-service",
        "context-tencentcloud-apigateway-api",
        "context-tencentcloud-cam-role",
        "context-tencentcloud-cam-policy",
    }

    routed_ids = {
        rule_id
        for rule_id in targeted_scan.DETECTION_TO_MISCONFIG
        if rule_id.startswith("context-tencentcloud-")
    }

    assert expected_ids <= routed_ids
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-tencentcloud-cvm-instance"] == []
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-tencentcloud-tke-cluster"] == [
        "Kubernetes/Workload",
        "Kubernetes/RBAC",
        "Kubernetes/Ingress",
        "Kubernetes/Service",
    ]


def test_huaweicloud_detection_rules_are_mapped():
    expected_ids = {
        "context-huaweicloud-ecs-instance",
        "context-huaweicloud-cce-cluster",
        "context-huaweicloud-cce-node-pool",
        "context-huaweicloud-obs-bucket",
        "context-huaweicloud-rds-instance",
        "context-huaweicloud-gaussdb-instance",
        "context-huaweicloud-kms-key",
        "context-huaweicloud-vpc",
        "context-huaweicloud-vpc-subnet",
        "context-huaweicloud-security-group",
        "context-huaweicloud-security-group-rule",
        "context-huaweicloud-elb-loadbalancer",
        "context-huaweicloud-apigw-instance",
        "context-huaweicloud-apigw-group",
        "context-huaweicloud-iam-group",
        "context-huaweicloud-iam-role",
    }

    routed_ids = {
        rule_id
        for rule_id in targeted_scan.DETECTION_TO_MISCONFIG
        if rule_id.startswith("context-huaweicloud-")
    }

    assert expected_ids <= routed_ids
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-huaweicloud-ecs-instance"] == []
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-huaweicloud-cce-cluster"] == [
        "Kubernetes/Workload",
        "Kubernetes/RBAC",
        "Kubernetes/Ingress",
        "Kubernetes/Service",
    ]


def test_run_opengrep_fails_fast_when_single_pass_detection_times_out(monkeypatch, tmp_path: Path):
    seen: dict[str, object] = {}

    monkeypatch.setattr(targeted_scan, "_tracked_file_count", lambda _target: 1)

    def fake_run(cmd, capture_output, text, timeout=None, **_kwargs):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(targeted_scan.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        targeted_scan.run_opengrep([targeted_scan.DETECTION], tmp_path, "Detection")

    assert exc.value.code == 1
    assert seen["timeout"] == 180
