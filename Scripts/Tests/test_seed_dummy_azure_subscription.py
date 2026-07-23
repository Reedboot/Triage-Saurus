from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Scripts" / "Harvest" / "seed_dummy_azure_subscription.py"
BANNED_TERMS = {
    "chaps",
    "bacs",
    "fx",
    "institution",
    "cop",
    "payuk",
    "clearbank",
    "cbinovation",
    "sts",
    "previewaks",
    "externalaks",
    "sharedaks",
    "prodgreen",
    "pipeline-customer",
    "banking",
}


def test_seed_dummy_azure_subscription_populates_cozo(tmp_path):
    db_path = tmp_path / "cozo.db"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db-path",
            str(db_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sub = conn.execute("SELECT id, display_name, environment, state FROM subscriptions").fetchone()
        assert sub is not None
        assert sub["display_name"] == "marketlane-demo"
        assert sub["environment"] == "dev"

        assets = conn.execute(
            "SELECT name, resource_group, COALESCE(fqdn, '') AS fqdn FROM provisioned_assets ORDER BY name"
        ).fetchall()
        assert len(assets) >= 44
        names = {row["name"] for row in assets}
        assert {
            "bas-marketlane-core",
            "fw-marketlane-core",
            "agw-marketlane-edge",
            "lb-marketlane-ingress",
            "snet-marketlane-bastion",
            "apim-marketlane-edge",
            "catalog-marketlane",
            "list-items",
            "create-order",
            "asp-marketlane-web",
            "ase-marketlane-shared",
            "listener-marketlane-web",
            "listener-marketlane-api",
            "product-marketlane-core",
            "sub-marketlane-core",
            "backend-marketlane-catalog",
            "nv-marketlane-catalog-url",
            "nodepool-marketlane-apps",
            "ingress-marketlane-api",
            "allow-marketlane-vnet",
            "cosmos-marketlane-orders",
            "snet-marketlane-apim",
            "snet-marketlane-aks",
            "snet-marketlane-ase",
            "snet-marketlane-appsvc",
        }.issubset(names)
        assert "waf-marketlane-edge" not in names

        app_gateway = conn.execute(
            """
            SELECT sku, waf_mode
            FROM provisioned_assets
            WHERE name = ?
            """,
            ("agw-marketlane-edge",),
        ).fetchone()
        assert tuple(app_gateway) == ("WAF_v2", "Prevention")

        conn_count = conn.execute("SELECT COUNT(*) FROM resource_connections").fetchone()[0]
        assert conn_count >= 40
        direct_apim_routes = conn.execute(
            """
            SELECT target.name
            FROM resource_connections rc
            JOIN provisioned_assets source ON source.rowid = rc.source_resource_id
            JOIN provisioned_assets target ON target.rowid = rc.target_resource_id
            WHERE source.name = ?
              AND rc.connection_type = 'routes_to'
            """,
            ("apim-marketlane-edge",),
        ).fetchall()
        assert direct_apim_routes == []

        assert conn.execute("SELECT COUNT(*) FROM appgw_routing_rules").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM appgw_waf_policies").fetchone()[0] == 0
        aks_routes = conn.execute(
            """
            SELECT cluster_name, namespace, ingress_name, host, exposure_level,
                   service_name, service_port, deployment_name
            FROM aks_routes
            ORDER BY namespace, service_name
            """
        ).fetchall()
        assert [tuple(row) for row in aks_routes] == [
            (
                "aks-marketlane-platform",
                "orders",
                "orders-ingress",
                "orders.marketlane-retail.internal",
                "Internal",
                "orders-api",
                "8080",
                "orders-api",
            ),
            (
                "aks-marketlane-platform",
                "storefront",
                "storefront-ingress",
                "store.marketlane-retail.internal",
                "Internal",
                "store-web",
                "80",
                "store-web",
            ),
        ]
        app_gateway_routes = conn.execute(
            """
            SELECT rule_name, hostname, backend_fqdns
            FROM appgw_routing_rules
            WHERE gateway_name = ?
            ORDER BY rule_name
            """,
            ("agw-marketlane-edge",),
        ).fetchall()
        assert [tuple(row) for row in app_gateway_routes] == [
            (
                "rule-marketlane-api",
                "api.marketlane-retail.com",
                '["apim-marketlane.azure-api.net"]',
            ),
            (
                "rule-marketlane-web",
                "shop.marketlane-retail.com",
                '["store.marketlane-retail.internal"]',
            ),
        ]
        apim_asset = conn.execute(
            """
            SELECT is_public, raw_json
            FROM provisioned_assets
            WHERE name = ?
            """,
            ("apim-marketlane-edge",),
        ).fetchone()
        assert apim_asset["is_public"] == 0
        assert json.loads(apim_asset["raw_json"])["properties"]["virtualNetworkType"] == "Internal"
        assert conn.execute("SELECT COUNT(*) FROM firewall_policies").fetchone()[0] >= 1
        assert conn.execute("SELECT COUNT(*) FROM firewall_app_rules").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM firewall_nat_rules").fetchone()[0] >= 1
        apim_routes = conn.execute(
            """
            SELECT r.api_name, r.backend_id, r.backend_url,
                   CASE WHEN b.id IS NULL THEN 0 ELSE 1 END AS backend_exists
            FROM apim_api_routes r
            LEFT JOIN apim_backends b
              ON b.subscription_id = r.subscription_id
             AND LOWER(b.apim_name) = LOWER(r.apim_name)
             AND LOWER(b.backend_id) = LOWER(r.backend_id)
            ORDER BY r.api_name
            """
        ).fetchall()
        assert [tuple(row) for row in apim_routes] == [
            (
                "catalog-marketlane",
                "store-backend",
                "https://store.marketlane-retail.azurewebsites.net",
                1,
            ),
            (
                "orders-marketlane",
                "aks-marketlane-platform-orders-orders-api-8080",
                "https://orders.marketlane-retail.internal/",
                1,
            ),
        ]
        assert conn.execute("SELECT COUNT(*) FROM apim_backends").fetchone()[0] == 2
        apim_operations = conn.execute(
            """
            SELECT api_name, operation_id, method, url_template
            FROM apim_api_operations
            ORDER BY api_name, operation_id
            """
        ).fetchall()
        assert [tuple(row) for row in apim_operations] == [
            ("catalog-marketlane", "get-item", "GET", "/items/{itemId}"),
            ("catalog-marketlane", "list-items", "GET", "/items"),
            ("catalog-marketlane", "search-items", "GET", "/items/search"),
            ("orders-marketlane", "cancel-order", "POST", "/orders/{orderId}/cancel"),
            ("orders-marketlane", "create-order", "POST", "/orders"),
            ("orders-marketlane", "get-order", "GET", "/orders/{orderId}"),
        ]

        text_blobs = " ".join(
            " ".join(str(row[col]) for col in ("name", "resource_group", "fqdn"))
            for row in assets
        ).lower()
        for term in BANNED_TERMS:
            assert term not in text_blobs
    finally:
        conn.close()


def test_seed_uses_configured_brand_for_orders_backend(tmp_path):
    db_path = tmp_path / "cozo.db"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db-path",
            str(db_path),
            "--display-name",
            "northwind-demo",
            "--brand",
            "northwind",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    conn = sqlite3.connect(db_path)
    try:
        backend_ids = {
            row[0]
            for row in conn.execute("SELECT backend_id FROM apim_backends")
        }
        route_backend_ids = {
            row[0]
            for row in conn.execute("SELECT backend_id FROM apim_api_routes")
        }
        expected = "aks-northwind-platform-orders-orders-api-8080"
        assert expected in backend_ids
        assert expected in route_backend_ids
        assert all("marketlane" not in backend_id for backend_id in backend_ids)
        web_route = conn.execute(
            """
            SELECT backend_fqdns
            FROM appgw_routing_rules
            WHERE gateway_name = ? AND rule_name = ?
            """,
            ("agw-northwind-edge", "rule-northwind-web"),
        ).fetchone()
        storefront_route = conn.execute(
            """
            SELECT host, exposure_level
            FROM aks_routes
            WHERE cluster_name = ? AND namespace = ?
            """,
            ("aks-northwind-platform", "storefront"),
        ).fetchone()
        assert web_route == ('["store.northwind-retail.internal"]',)
        assert storefront_route == ("store.northwind-retail.internal", "Internal")
    finally:
        conn.close()
