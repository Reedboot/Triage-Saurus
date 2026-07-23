#!/usr/bin/env python3
"""Tests for icon resolver coverage."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Generate"))

from icon_resolver import get_icon_path, build_icon_map_bulk


def test_provider_specific_icon_paths_exist():
    assert get_icon_path("aws_ebs_volume", "aws").as_posix().endswith(
        "web/static/assets/icons/aws/Arch_Storage/64/elastic-block-store.svg"
    )
    assert get_icon_path("aws_network_interface", "aws").as_posix().endswith(
        "web/static/assets/icons/aws/Arch_Compute/64/ec2.svg"
    )
    assert get_icon_path("aws_internet_gateway", "aws").as_posix().endswith(
        "web/static/assets/icons/aws/Arch_Networking-Content-Delivery/64/global-accelerator.svg"
    )
    assert get_icon_path("aws_subnet", "aws").as_posix().endswith(
        "web/static/assets/icons/aws/public-subnet.svg"
    )
    assert get_icon_path("aws_route_table", "aws").as_posix().endswith(
        "web/static/assets/icons/aws/Arch_Networking-Content-Delivery/route-table.svg"
    )
    assert get_icon_path("google_compute_disk", "gcp").as_posix().endswith(
        "web/static/assets/icons/gcp/Hyperdisk/SVG/hyperdisk.svg"
    )
    assert get_icon_path("azurerm_sql_server", "azure").as_posix().endswith(
        "web/static/assets/icons/azure/databases/sql-server.svg"
    )
    assert get_icon_path("alicloud_db_instance", "alicloud").as_posix().endswith(
        "web/static/assets/icons/alicloud/database/db-instance.svg"
    )
    assert get_icon_path("alicloud_api_gateway_api", "alicloud").as_posix().endswith(
        "web/static/assets/icons/alicloud/api/api-gateway.svg"
    )
    assert get_icon_path("oci_database_db_system", "oci").as_posix().endswith(
        "web/static/assets/icons/oci/database/db-system.svg"
    )
    assert get_icon_path("oci_core_nat_gateway", "oci").as_posix().endswith(
        "web/static/assets/icons/oci/networking/nat-gateway.svg"
    )
    assert get_icon_path("oci_kms_vault", "oci").as_posix().endswith(
        "web/static/assets/icons/oci/security/vault.svg"
    )
    assert get_icon_path("kubernetes_serviceaccount", "kubernetes").as_posix().endswith(
        "web/static/assets/icons/kubernetes/serviceaccount.svg"
    )
    assert get_icon_path("kubernetes_service", "kubernetes").as_posix().endswith(
        "web/static/assets/icons/azure/containers/kubernetes-service.svg"
    )


def test_provider_icon_maps_include_new_sets():
    aws_map = build_icon_map_bulk("aws")
    gcp_map = build_icon_map_bulk("gcp")
    alicloud_map = build_icon_map_bulk("alicloud")
    oci_map = build_icon_map_bulk("oci")
    kubernetes_map = build_icon_map_bulk("kubernetes")

    assert "aws_ebs_volume" in aws_map
    assert "google_compute_disk" in gcp_map
    assert "alicloud_db_instance" in alicloud_map
    assert "alicloud_actiontrail_trail" in alicloud_map
    assert "oci_database_db_system" in oci_map
    assert "oci_core_nat_gateway" in oci_map
    assert "oci_network_load_balancer_network_load_balancer" in oci_map
    assert "oci_containerengine_node_pool" in oci_map
    assert "kubernetes_serviceaccount" in kubernetes_map
    assert kubernetes_map["kubernetes_service"].endswith("azure/containers/kubernetes-service.svg")


def test_alicloud_and_oci_maps_are_provider_isolated():
    alicloud_map = build_icon_map_bulk("alicloud")
    oci_map = build_icon_map_bulk("oci")

    assert not any(k.startswith("oci_") for k in alicloud_map)
    assert not any(k.startswith("alicloud_") for k in oci_map)
    assert all(v.startswith("/static/assets/icons/alicloud/") for k, v in alicloud_map.items() if k != "synthetic_sql_server" and k != "synthetic_database" and k != "synthetic_storage" and k != "synthetic_server")
    assert all(v.startswith("/static/assets/icons/oci/") for k, v in oci_map.items() if k != "synthetic_sql_server" and k != "synthetic_database" and k != "synthetic_storage" and k != "synthetic_server")


def test_core_alicloud_resource_types_have_icons():
    alicloud_map = build_icon_map_bulk("alicloud")

    required = {
        "alicloud_actiontrail_trail",
        "alicloud_instance",
        "alicloud_cs_managed_kubernetes",
        "alicloud_cs_kubernetes_node_pool",
        "alicloud_oss_bucket",
        "alicloud_oss_bucket_object",
        "alicloud_db_instance",
        "alicloud_kms_key",
        "alicloud_kms_secret",
        "alicloud_vpc",
        "alicloud_vswitch",
        "alicloud_security_group",
        "alicloud_security_group_rule",
        "alicloud_ram_role",
        "alicloud_ram_policy",
        "alicloud_ram_role_attachment",
        "alicloud_ram_role_policy_attachment",
        "alicloud_ram_access_key",
        "alicloud_api_gateway_api",
        "alicloud_api_gateway_app",
        "alicloud_api_gateway_group",
        "alicloud_log_project",
        "alicloud_log_store",
        "alicloud_slb_load_balancer",
        "alicloud_alb_load_balancer",
        "alicloud_fc_function",
        "alicloud_kvstore_instance",
    }

    assert required.issubset(set(alicloud_map))


def test_core_aws_and_gcp_resource_types_have_icons():
    aws_map = build_icon_map_bulk("aws")
    gcp_map = build_icon_map_bulk("gcp")

    assert {"aws_ebs_volume", "aws_s3_bucket", "aws_instance"}.issubset(set(aws_map))
    assert {"google_compute_disk", "google_storage_bucket", "google_compute_instance"}.issubset(set(gcp_map))
