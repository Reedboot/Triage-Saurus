from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from subscription_diagram_helpers import (
    build_subscription_diagrams_by_rg,
    subscription_assets_from_rows,
    subscription_asset_tier,
    subscription_apply_plan_hierarchy,
    subscription_node_id,
)


def test_subscription_asset_tier_includes_threat_model_relevant_resources():
    assert subscription_asset_tier("Microsoft.ManagedIdentity/userAssignedIdentities") == "data"
    assert subscription_asset_tier("Microsoft.KeyVault/vaults") == "data"
    assert subscription_asset_tier("Microsoft.Logic/workflows") == "backend"
    assert subscription_asset_tier("Microsoft.EventGrid/topics") == "backend"
    assert subscription_asset_tier("Microsoft.Kusto/clusters") == "data"
    assert subscription_asset_tier("Microsoft.Databricks/workspaces") == "backend"


def test_subscription_asset_tier_keeps_monitoring_noise_hidden():
    assert subscription_asset_tier("Microsoft.Insights/components") == "other"
    assert subscription_asset_tier("Microsoft.Insights/actionGroups") == "other"
    assert subscription_asset_tier("Microsoft.Insights/activityLogAlerts") == "other"


def test_subscription_apply_plan_hierarchy_inherits_ase_network():
    rows = [
        (
            "ase-one",
            "Microsoft.Web/hostingEnvironments",
            "rg-app",
            "ase-one.appserviceenvironment.net",
            0,
            "ASEv3",
            "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{"virtualNetwork":{"id":"/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet"},"subnet":{"id":"/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet/subnets/ase-subnet"}}}',
            None,
            None,
        ),
        (
            "plan-one",
            "Microsoft.Web/serverfarms",
            "rg-app",
            "",
            0,
            "P1v3",
            "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one",
            0,
            None,
            0,
            None,
            None,
            '{"hostingEnvironmentProfile":{"id":"/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"}}',
            None,
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, lambda arm_type: arm_type.split("/")[-1])
    plan = next(item for item in assets if item["name"] == "plan-one")

    assert plan["vnet_name"] == "prod-vnet"
    assert plan["subnet_name"] == "ase-subnet"


def test_subscription_apply_plan_hierarchy_inherits_hosted_site_network():
    subnet_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Network/virtualNetworks/app-vnet/subnets/app-subnet"
    rows = [
        (
            "web-prod",
            "Microsoft.Web/sites",
            "rg-app",
            "web-prod.azurewebsites.net",
            1,
            None,
            "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/web-prod",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{"siteConfig":{"virtualNetworkSubnetId":"%s"}}}' % subnet_id,
            None,
            None,
        ),
        (
            "plan-one",
            "Microsoft.Web/serverfarms",
            "rg-app",
            "",
            0,
            "P1v3",
            "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
    ]

    assets = subscription_assets_from_rows(rows, lambda arm_type: arm_type.split("/")[-1])
    merged = subscription_apply_plan_hierarchy(assets, [("rg-app", "web-prod", "rg-app", "plan-one")])
    plan = next(item for item in merged if item["name"] == "plan-one")

    assert plan["vnet_name"] == "app-vnet"
    assert plan["parent_vnet_name"] == "app-vnet"
    assert plan["subnet_name"] == "app-subnet"
    assert plan["subnet_id"] == subnet_id


def test_aks_route_backend_prefers_ingress_hostname_over_cluster():
    rows = [
        (
            "portalui",
            "Microsoft.Web/sites",
            "rg-aks",
            "portalui.azurewebsites.net",
            0,
            "Standard",
            "/subscriptions/sub-1/resourceGroups/rg-aks/providers/Microsoft.Web/sites/portalui",
            0,
            None,
            0,
            None,
            '[{"target":"production-portalui2.internal.cbinnovation.uk","name":"production-portalui2.internal.cbinnovation.uk"}]',
            '{"properties":{}}',
            None,
            None,
        ),
        (
            "SharedAKS",
            "Microsoft.ContainerService/managedClusters",
            "rg-aks",
            "",
            0,
            "Standard",
            "/subscriptions/sub-1/resourceGroups/rg-aks/providers/Microsoft.ContainerService/managedClusters/SharedAKS",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
    ]
    aks_route_rows = [
        (
            "SharedAKS",
            "default",
            "portalui-ingress",
            "production-portalui2.internal.cbinnovation.uk",
            "/*",
            "Internal",
            "portalui",
            80,
            "portalui",
            "git@example.com/portalui",
            "rg-aks",
            "{}",
        )
    ]

    diagrams = build_subscription_diagrams_by_rg(
        "pipeline-customer-production",
        "production",
        rows,
        sanitise_node_id=lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
        friendly_type=lambda arm_type: arm_type.split("/")[-1],
        get_icon_path=lambda arm_type: None,
        normalize_attack_paths=lambda value, reviewer=None: [],
        aks_route_rows=aks_route_rows,
    )

    mermaid = diagrams[0]["mermaid"]
    portalui_nid = subscription_node_id(
        {"name": "portalui", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )
    ingress_nid = subscription_node_id(
        {"name": "SharedAKS-default-portalui-ingress-production-portalui2.internal.cbinnovation.uk-ingress", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )
    service_nid = subscription_node_id(
        {"name": "SharedAKS-default-portalui-80", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )
    cluster_nid = subscription_node_id(
        {"name": "SharedAKS", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )

    assert "production-portalui2.internal.cbinnovation.uk" in mermaid
    assert f"{portalui_nid} --> {ingress_nid}" in mermaid
    assert f"{ingress_nid} --> {cluster_nid}" in mermaid
    assert f"{service_nid}[" not in mermaid


def test_kubernetes_service_nodes_are_hidden_when_orphaned():
    rows = [
        (
            "SharedAKS",
            "Microsoft.ContainerService/managedClusters",
            "rg-aks",
            "",
            0,
            "Standard",
            "/subscriptions/sub-1/resourceGroups/rg-aks/providers/Microsoft.ContainerService/managedClusters/SharedAKS",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
        (
            "portalui",
            "Microsoft.Kubernetes/services",
            "rg-aks",
            "",
            0,
            None,
            "/subscriptions/sub-1/resourceGroups/rg-aks/providers/Microsoft.Kubernetes/services/portalui",
            0,
            None,
            0,
            None,
            None,
            '{"properties":{}}',
            None,
            None,
        ),
    ]

    diagrams = build_subscription_diagrams_by_rg(
        "pipeline-customer-production",
        "production",
        rows,
        sanitise_node_id=lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
        friendly_type=lambda arm_type: arm_type.split("/")[-1],
        get_icon_path=lambda arm_type: None,
        normalize_attack_paths=lambda value, reviewer=None: [],
    )

    mermaid = diagrams[0]["mermaid"]
    service_nid = subscription_node_id(
        {"name": "portalui", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )
    cluster_nid = subscription_node_id(
        {"name": "SharedAKS", "rg": "rg-aks"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )

    assert f"{service_nid}[" not in mermaid
    assert cluster_nid in mermaid


def test_apim_backend_target_routes_to_target_service():
    import json

    rows = [
        (
            "apim-prod",
            "Microsoft.ApiManagement/service",
            "rg-api",
            "apim.example.com",
            1,
            "Developer",
            "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod",
            0,
            None,
            0,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
        (
            "apim-prod::backend1",
            "APIM Backend Target",
            "rg-api",
            None,
            0,
            None,
            "apim-prod::backend1",
            0,
            None,
            0,
            None,
            None,
            json.dumps({
                "apim_name": "apim-prod",
                "backend_id": "backend1",
                "backend_url": "https://backend1.azurewebsites.net",
                "_extra": {"display_label": "backend1"},
            }),
            None,
            None,
        ),
        (
            "backend1",
            "Microsoft.Web/sites",
            "rg-api",
            "backend1.azurewebsites.net",
            1,
            "P1v3",
            "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.Web/sites/backend1",
            0,
            None,
            0,
            None,
            None,
            json.dumps({"properties": {}}),
            None,
            None,
        ),
    ]

    diagrams = build_subscription_diagrams_by_rg(
        "Test Subscription",
        "production",
        rows,
        sanitise_node_id=lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
        friendly_type=lambda arm_type: arm_type.split("/")[-1],
        get_icon_path=lambda _resource_type: None,
        normalize_attack_paths=lambda raw_paths, reviewer=None: raw_paths,
    )

    mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
    backend_target_nid = subscription_node_id(
        {"name": "apim-prod::backend1", "rg": "rg-api"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )
    function_nid = subscription_node_id(
        {"name": "backend1", "rg": "rg-api"},
        lambda value: value.replace("-", "_").replace("/", "_").replace(".", "_"),
    )

    assert f"{backend_target_nid} --> {function_nid}" in mermaid, mermaid
