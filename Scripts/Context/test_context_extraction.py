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

    assert func.parent == "azurerm_service_plan.plan"


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
