#!/usr/bin/env python3
"""Regression tests for Azure scan routing."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for rel in ("Generate", "Context", "Scan", "Persist", "Utils"):
    sys.path.insert(0, str(ROOT / "Scripts" / rel))

import targeted_scan


def test_new_azure_alias_rules_route_to_expected_folders():
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-azure-function-app"] == ["Azure/AppService"]
    assert targeted_scan.DETECTION_TO_MISCONFIG["context-azure-virtual-machine"] == ["Azure/VM", "Azure/Compute"]
