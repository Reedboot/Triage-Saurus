#!/usr/bin/env python3
"""Regression tests for scan routing mappings."""

import json
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


def test_aws_network_association_rules_route_to_expected_folders():
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-aws-route-table-association"] == ["AWS/SecurityGroup"]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-aws-network-interface-attachment"] == [
        "AWS/EC2",
        "AWS/SecurityGroup",
    ]


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


def test_run_opengrep_falls_back_to_chunked_scan_after_detection_timeout(monkeypatch, tmp_path: Path):
    seen: dict[str, object] = {"calls": 0}

    monkeypatch.setattr(targeted_scan, "_tracked_file_count", lambda _target: 1)
    monkeypatch.setattr(
        targeted_scan,
        "_build_scan_chunks",
        lambda _target, max_files, max_paths: [["a.tf"], ["b.tf"]],
    )

    def fake_run(cmd, capture_output, text, timeout=None, cwd=None, **_kwargs):
        seen["calls"] += 1
        if cwd is None:
            seen["timeout"] = timeout
            raise subprocess.TimeoutExpired(cmd, timeout)

        if seen["calls"] == 2:
            payload = {"results": [{"check_id": "chunk-1"}], "errors": [], "paths": {"scanned": ["a.tf"]}}
        else:
            payload = {"results": [{"check_id": "chunk-2"}], "errors": [], "paths": {"scanned": ["b.tf"]}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(targeted_scan.subprocess, "run", fake_run)

    data = targeted_scan.run_opengrep([targeted_scan.DETECTION], tmp_path, "Detection")

    assert seen["timeout"] == 180
    assert seen["calls"] == 3
    assert [result["check_id"] for result in data["results"]] == ["chunk-1", "chunk-2"]
    assert data["paths"]["scanned"] == ["a.tf", "b.tf"]
