from __future__ import annotations

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
            "listener-marketlane-web",
            "listener-marketlane-api",
            "waf-marketlane-edge",
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
            "snet-marketlane-appsvc",
        }.issubset(names)

        conn_count = conn.execute("SELECT COUNT(*) FROM resource_connections").fetchone()[0]
        assert conn_count >= 40

        assert conn.execute("SELECT COUNT(*) FROM appgw_routing_rules").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM appgw_waf_policies").fetchone()[0] >= 1
        assert conn.execute("SELECT COUNT(*) FROM apim_api_routes").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM apim_api_operations").fetchone()[0] >= 4

        text_blobs = " ".join(
            " ".join(str(row[col]) for col in ("name", "resource_group", "fqdn"))
            for row in assets
        ).lower()
        for term in BANNED_TERMS:
            assert term not in text_blobs
    finally:
        conn.close()
