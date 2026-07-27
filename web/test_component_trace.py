import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web.app as app_module


def test_component_trace_returns_full_chain_for_aks_service(monkeypatch, tmp_path):
    db_path = tmp_path / "trace.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE subscriptions (id TEXT PRIMARY KEY, display_name TEXT, environment TEXT, state TEXT);
        CREATE TABLE provisioned_assets (
            id TEXT PRIMARY KEY, subscription_id TEXT, resource_group TEXT, name TEXT,
            type TEXT, fqdn TEXT, raw_json TEXT, last_synced TEXT
        );
        CREATE TABLE appgw_routing_rules (
            id TEXT PRIMARY KEY, subscription_id TEXT, gateway_name TEXT, gateway_resource_id TEXT,
            resource_group TEXT, rule_name TEXT, listener_name TEXT, hostname TEXT, protocol TEXT,
            url_path TEXT, backend_pool_name TEXT, backend_fqdns TEXT, http_settings_name TEXT,
            backend_port INTEGER, backend_protocol TEXT, host_override TEXT, waf_policy_name TEXT,
            exposure_level TEXT
        );
        CREATE TABLE apim_api_routes (
            id TEXT PRIMARY KEY, subscription_id TEXT, apim_name TEXT, api_name TEXT,
            api_display_name TEXT, api_path TEXT, api_protocols TEXT, backend_id TEXT,
            backend_url TEXT, service_url TEXT, requires_subscription INTEGER,
            gateway_hosts TEXT, exposure_level TEXT, last_synced TEXT
        );
        CREATE TABLE apim_backends (
            id TEXT PRIMARY KEY, subscription_id TEXT, apim_name TEXT, backend_id TEXT,
            title TEXT, description TEXT, url TEXT, protocol TEXT
        );
        CREATE TABLE aks_routes (
            id TEXT PRIMARY KEY, subscription_id TEXT, cluster_name TEXT, namespace TEXT,
            ingress_name TEXT, host TEXT, host_aliases TEXT, path TEXT, service_name TEXT,
            service_port TEXT, service_ports TEXT, deployment_name TEXT,
            deployment_namespace TEXT, git_repository TEXT, team TEXT, resource_group TEXT,
            exposure_level TEXT
        );
        """
    )
    conn.execute("INSERT INTO subscriptions VALUES ('sub-1', 'Test', 'prod', 'Enabled')")
    conn.execute(
        "INSERT INTO provisioned_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("apim-1", "sub-1", "rg", "apim-prod", "Microsoft.ApiManagement/service",
         "apim-prod.azure-api.net", "{}", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO appgw_routing_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("route-1", "sub-1", "appgw", "gw-1", "rg", "rule", "listener",
         "api.example.com", "HTTPS", "/*", "pool", json.dumps(["apim-prod.azure-api.net"]),
         "https", 443, "HTTPS", None, None, "Public"),
    )
    conn.execute(
        "INSERT INTO apim_api_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("api-route", "sub-1", "apim-prod", "api", "API", "/api", '["https"]',
         "backend-1", "https://api.internal", "https://api.internal", 1,
         json.dumps(["apim-prod.azure-api.net"]), "Public", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO apim_backends VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("backend", "sub-1", "apim-prod", "backend-1", "API backend", "Backend",
         "https://api.internal", "https"),
    )
    conn.execute(
        "INSERT INTO aks_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("aks-route", "sub-1", "aks-prod", "default", "api-ingress", "api.internal",
         "[]", "/*", "api-service", "80", "[80]", "api-deployment", "default",
         "repo", "platform", "rg", "Internal"),
    )
    conn.commit()

    monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
    response = app_module.app.test_client().get(
        "/api/subscriptions/sub-1/trace-component",
        query_string={"component_name": "api-service", "component_type": "kubernetes_service"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["matched_count"] == 1
    assert [step["kind"] for step in payload["traces"][0]["chain"]] == [
        "internet", "listener", "appgw", "backend_pool", "apim_api",
        "apim_service", "apim_backend", "aks_ingress", "aks_service",
        "aks_deployment", "aks_cluster",
    ]
    assert payload["traces"][0]["mermaid"].startswith("flowchart LR")
    assert "class='ni'" in payload["traces"][0]["mermaid"]
    assert "icon_kubernetes_service" in payload["traces"][0]["mermaid"]
    assert "classDef icon_kubernetes_service" in payload["traces"][0]["mermaid"]
    conn.close()
