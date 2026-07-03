#!/usr/bin/env python3
"""Regression tests for Azure Bastion hierarchy detection."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Context"))

from external_resource_hierarchy import HIERARCHY_CONFIG


def test_bastion_prefers_subnet_parent():
    mapping = HIERARCHY_CONFIG["azurerm_bastion_host"]
    assert mapping["parent_type"].startswith("azurerm_subnet")
    assert mapping["parent_field"] == "subnet_id"
