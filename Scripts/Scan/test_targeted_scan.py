#!/usr/bin/env python3
"""Regression tests for scan routing mappings."""

import json
from pathlib import Path
import subprocess
import sys

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


def test_chunk_progress_log_is_compact():
    msg = targeted_scan._format_chunk_progress(
        ["a.tf", "b.tf", "c.tf", "d.tf", "e.tf"], idx=1, total=3
    )
    assert msg == "  [chunk 1/3] scanning 5 paths: a.tf, b.tf, c.tf, +2 more"


def test_chunk_progress_log_handles_repo_root():
    msg = targeted_scan._format_chunk_progress(["."], idx=2, total=2)
    assert msg == "  [chunk 2/2] scanning repository root (.)"


def test_dotnet_runtime_dependency_rules_are_mapped():
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-dotnet-appconfig-servicebus-endpoint-reference"] == [
        "Azure/ServiceBus",
        "Secrets",
    ]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-dotnet-csharp-apim-operation-call"] == [
        "Secrets",
    ]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-dotnet-csharp-cosmos-config-reference"] == [
        "Azure/CosmosDB",
        "Secrets",
    ]


def test_appconfig_resource_type_normalization_maps_generic_connection_types():
    assert (
        targeted_scan._normalize_detected_resource(
            "context-azure-servicebus-connection",
            "connection_string",
        )
        == "azurerm_servicebus_namespace"
    )
    assert (
        targeted_scan._normalize_detected_resource(
            "context-azure-storage-connection",
            "connection_string",
        )
        == "azurerm_storage_account"
    )


def test_run_opengrep_falls_back_to_chunked_scan_after_detection_timeout(monkeypatch, tmp_path: Path):
    seen: dict[str, object] = {"calls": 0}

    monkeypatch.setattr(targeted_scan, "_tracked_file_count", lambda _target: 1)
    monkeypatch.setattr(targeted_scan, "_scannable_paths", lambda _target: ["a.tf", "b.tf"])
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


def test_filter_scannable_paths_excludes_generated_assets():
    paths = [
        "src/app.py",
        "docs/readme.md",
        "frontend/package-lock.json",
        "frontend/yarn.lock",
        "frontend/src/app.min.js",
        "frontend/src/app.min.css",
        "frontend/src/app.js.map",
        "modules/module-1/resources/storage_account/webfiles/build/static/js/main.js",
        "modules/module-1/resources/storage_account/webfiles/src/index.js",
    ]

    assert targeted_scan._filter_scannable_paths(paths) == [
        "src/app.py",
        "modules/module-1/resources/storage_account/webfiles/src/index.js",
    ]


def test_build_scan_chunks_uses_requested_chunk_size():
    files = [f"src/file-{idx}.py" for idx in range(5)]

    assert targeted_scan._build_scan_chunks(files, max_files=2) == [
        ["src/file-0.py", "src/file-1.py"],
        ["src/file-2.py", "src/file-3.py"],
        ["src/file-4.py"],
    ]


def test_run_opengrep_uses_single_pass_for_small_scannable_set(monkeypatch):
    monkeypatch.setattr(targeted_scan, "_tracked_file_count", lambda _target: 1826)
    monkeypatch.setattr(
        targeted_scan,
        "_scannable_paths",
        lambda _target: [f"src/file-{idx}.py" for idx in range(151)],
    )

    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, cwd=None, check=False):
        del capture_output, text, timeout, cwd, check
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"results":[],"errors":[],"paths":{"scanned":[]}}',
            stderr="",
        )

    monkeypatch.setattr(targeted_scan.subprocess, "run", fake_run)

    targeted_scan.run_opengrep([targeted_scan.DETECTION], Path("/repo"), "Detection")

    assert calls == [[
        "opengrep",
        "scan",
        "--config",
        str(targeted_scan.DETECTION),
        "/repo",
        "--json",
        "--quiet",
    ]]


def test_run_opengrep_chunks_large_scannable_set(monkeypatch):
    monkeypatch.setattr(targeted_scan, "_tracked_file_count", lambda _target: 1826)
    scannable = [f"src/file-{idx}.py" for idx in range(801)]
    monkeypatch.setattr(targeted_scan, "_scannable_paths", lambda _target: scannable)

    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout, cwd=None, check=False):
        del capture_output, text, timeout, check
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"results":[],"errors":[],"paths":{"scanned":[]}}',
            stderr="",
        )

    monkeypatch.setattr(targeted_scan.subprocess, "run", fake_run)

    targeted_scan.run_opengrep([targeted_scan.DETECTION], Path("/repo"), "Detection")

    assert len(calls) == 5
    assert all(call[:4] == ["opengrep", "scan", "--config", str(targeted_scan.DETECTION)] for call in calls)
    assert all(call[-2:] == ["--json", "--quiet"] for call in calls)
    assert all(len(call[4:-2]) <= 200 for call in calls[:-1])
    assert len(calls[-1][4:-2]) == 1
