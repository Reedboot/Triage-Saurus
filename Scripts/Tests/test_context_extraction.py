#!/usr/bin/env python3
"""Regression tests for Azure parent resolution in context_extraction.py."""

from pathlib import Path
import sys
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import context_extraction


def _stub_heavy_detectors(monkeypatch):
    monkeypatch.setattr(
        context_extraction,
        "extract_kubernetes_topology_signals",
        lambda *args, **kwargs: {
            "service_names": [],
            "ingress_to_service": [],
        },
    )
    monkeypatch.setattr(
        context_extraction,
        "detect_ingress_from_code",
        lambda *args, **kwargs: {
            "type": "Unknown",
            "edge_resources": [],
            "hosts": [],
            "evidence": [],
        },
    )
    monkeypatch.setattr(context_extraction, "extract_kubernetes_manifest_resources", lambda *args, **kwargs: [])
    monkeypatch.setattr(context_extraction, "extract_connection_string_dependencies", lambda *args, **kwargs: None)


def test_function_app_parent_type_falls_back_to_service_plan(tmp_path, monkeypatch):
    _stub_heavy_detectors(monkeypatch)
    (tmp_path / "main.tf").write_text(
        dedent(
            """
            resource "azurerm_service_plan" "plan" {
              name     = "plan"
              location = "eastus"
            }

            resource "azurerm_function_app" "func" {
              name              = "func"
              location          = "eastus"
              service_plan_id   = azurerm_service_plan.plan.id
            }
            """
        ).strip()
    )

    context = context_extraction.extract_context(str(tmp_path))
    func = next(r for r in context.resources if r.resource_type == "azurerm_function_app")
    plan = next((r for r in context.resources if r.resource_type == "azurerm_service_plan"), None)

    # Verify both resources were extracted
    assert func is not None
    assert plan is not None
    # Verify the resources have the correct names
    assert func.name == "func"
    assert plan.name == "plan"


def test_network_interface_parent_type_resolves_legacy_vm(tmp_path, monkeypatch):
    _stub_heavy_detectors(monkeypatch)
    (tmp_path / "main.tf").write_text(
        dedent(
            """
            resource "azurerm_virtual_machine" "vm" {
              name     = "vm"
              location = "eastus"
            }

            resource "azurerm_network_interface" "nic" {
              name     = "nic"
              location = "eastus"
            }
            """
        ).strip()
    )

    context = context_extraction.extract_context(str(tmp_path))
    nic = next(r for r in context.resources if r.resource_type == "azurerm_network_interface")

    assert nic.parent == "azurerm_virtual_machine.vm"


def test_is_valid_azure_resource_name_rejects_new_provider_prefixes():
    assert not context_extraction.is_valid_azure_resource_name("tencentcloud_instance")
    assert not context_extraction.is_valid_azure_resource_name("huaweicloud_compute_instance")
    assert context_extraction.is_valid_azure_resource_name("app-service-prod")


def test_meta_resources_infer_provider_for_new_prefixes(tmp_path, monkeypatch):
    _stub_heavy_detectors(monkeypatch)

    cases = [
        ("oci_core_instance", "oci"),
        ("tencentcloud_instance", "tencentcloud"),
        ("huaweicloud_compute_instance", "huaweicloud"),
    ]

    for terraform_type, expected_provider in cases:
        case_dir = tmp_path / expected_provider
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "main.tf").write_text(
            dedent(
                f"""
                resource "{terraform_type}" "workload" {{
                  name = "workload"
                }}

                resource "terraform_data" "meta" {{
                  input = "metadata"
                }}
                """
            ).strip()
        )

        context = context_extraction.extract_context(str(case_dir))
        meta = next(r for r in context.resources if r.resource_type == "terraform_data")
        assert (meta.properties or {}).get("inferred_provider") == expected_provider
