#!/usr/bin/env python3
"""Tests for icon resolver coverage."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Generate"))

from icon_resolver import get_icon_path, build_icon_map_bulk


def test_provider_specific_icon_paths_exist():
    assert get_icon_path("alicloud_db_instance", "alicloud").as_posix().endswith(
        "web/static/assets/icons/alicloud/database/db-instance.svg"
    )
    assert get_icon_path("oci_database_db_system", "oci").as_posix().endswith(
        "web/static/assets/icons/oci/database/db-system.svg"
    )
    assert get_icon_path("kubernetes_serviceaccount", "kubernetes").as_posix().endswith(
        "web/static/assets/icons/kubernetes/serviceaccount.svg"
    )


def test_provider_icon_maps_include_new_sets():
    alicloud_map = build_icon_map_bulk("alicloud")
    oci_map = build_icon_map_bulk("oci")
    kubernetes_map = build_icon_map_bulk("kubernetes")

    assert "alicloud_db_instance" in alicloud_map
    assert "oci_database_db_system" in oci_map
    assert "kubernetes_serviceaccount" in kubernetes_map
