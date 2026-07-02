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


def test_network_and_observability_arm_types_map_to_expected_catalog_entries():
    lb = resource_type_db.get_resource_type(None, "Microsoft.Network/loadBalancers")
    assert lb["friendly_name"] == "Load Balancer"
    assert lb["category"] == "Network"

    insights = resource_type_db.get_resource_type(None, "Microsoft.Insights/components")
    assert insights["friendly_name"] == "Application Insights"
    assert insights["category"] == "Monitoring"


def test_front_door_endpoint_arm_type_is_catalogued():
    endpoint = resource_type_db.get_resource_type(None, "Microsoft.Cdn/profiles/afdendpoints")
    assert endpoint["friendly_name"] == "Front Door Endpoint"
    assert endpoint["category"] == "Network"

    endpoint2 = resource_type_db.get_resource_type(None, "Microsoft.Cdn/profiles/endpoints")
    assert endpoint2["friendly_name"] == "Front Door Endpoint"
    assert endpoint2["category"] == "Network"


def test_new_azure_arm_types_map_to_expected_catalog_entries():
    slot = resource_type_db.get_resource_type(None, "Microsoft.Web/sites/slots")
    assert slot["friendly_name"] == "Web App Slot"
    assert slot["category"] == "Compute"
    assert slot["parent_type"] == "azurerm_app_service|azurerm_function_app|azurerm_linux_web_app"

    workflow = resource_type_db.get_resource_type(None, "Microsoft.Logic/workflows")
    assert workflow["friendly_name"] == "Logic Workflow"
    assert workflow["category"] == "Serverless"

    databricks = resource_type_db.get_resource_type(None, "Microsoft.Databricks/workspaces")
    assert databricks["friendly_name"] == "Databricks Workspace"
    assert databricks["category"] == "Compute"

    kusto = resource_type_db.get_resource_type(None, "Microsoft.Kusto/clusters")
    assert kusto["friendly_name"] == "Kusto Cluster"
    assert kusto["category"] == "Database"

    search = resource_type_db.get_resource_type(None, "Microsoft.Search/searchServices")
    assert search["friendly_name"] == "Search Service"
    assert search["category"] == "Database"


def test_virtual_machine_arm_type_is_catalogued():
    vm = resource_type_db.get_resource_type(None, "Microsoft.Compute/virtualMachines")
    assert vm["friendly_name"] == "Virtual Machine"
    assert vm["category"] == "Compute"
