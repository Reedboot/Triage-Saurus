#!/usr/bin/env python3
"""Regression tests for Terraform module source detection metadata."""

from pathlib import Path
import sys
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Context"))

import detect_module_sources


def test_detect_modules_returns_reference_file_line_and_url(tmp_path):
    terraform_dir = tmp_path / "terraform"
    terraform_dir.mkdir(parents=True, exist_ok=True)
    (terraform_dir / "main.tf").write_text(
        dedent(
            """
            module "payments_api" {
              source = "git::https://example.com/org/terraform-payments//modules/api"
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    detection = detect_module_sources.detect_modules(str(tmp_path))
    modules = detection["modules"]

    assert len(modules) == 1
    assert modules[0]["name"] == "payments_api"
    assert modules[0]["source"] == "git::https://example.com/org/terraform-payments//modules/api"
    assert modules[0]["source_file"] == "terraform/main.tf"
    assert modules[0]["source_line"] == 1


def test_detect_modules_is_repo_path_driven_not_repo_name_specific(tmp_path):
    (tmp_path / "main.tf").write_text(
        dedent(
            """
            module "custom_business_module" {
              source = "git::https://gitlab.example.com/platform/infra-modules//network"
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    detection = detect_module_sources.detect_modules(str(tmp_path))
    modules = detection["modules"]

    assert len(modules) == 1
    assert modules[0]["name"] == "custom_business_module"
    assert modules[0]["source"].startswith("git::https://gitlab.example.com")
    assert modules[0]["source_file"] == "main.tf"
    assert modules[0]["source_line"] == 1
