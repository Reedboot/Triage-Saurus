from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

import resource_type_db


def test_service_bus_arm_namespace_maps_to_canonical_service_bus_namespace():
    info = resource_type_db.get_resource_type(None, "Microsoft.ServiceBus/Namespaces")
    assert info["friendly_name"] == "Service Bus Namespace"
    assert info["category"] == "Messaging"
    assert info["provider"] == "azure"

    pattern_name, pattern = resource_type_db.get_service_pattern("Microsoft.ServiceBus/Namespaces")
    assert pattern_name == "messaging"
    assert pattern["providers"]["azure_servicebus"]["parent"] == "azurerm_servicebus_namespace"
