from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from subscription_diagram_helpers import (
    subscription_assets_from_rows,
    subscription_asset_tier,
    subscription_apply_plan_hierarchy,
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
