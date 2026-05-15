#!/usr/bin/env python3

import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Context"))

from module_registry import (  # noqa: E402
    ModuleMetadata,
    capture_module_findings,
    get_module_findings,
    record_module_usage,
    register_module,
)


def test_capture_module_findings_skips_when_findings_table_missing(tmp_path):
    db_path = tmp_path / "cozo.db"
    count = capture_module_findings(
        module_source="git::https://example.com/org/terraform-network",
        module_experiment_id="exp-123",
        db_path=str(db_path),
    )
    assert count == 0


def test_capture_module_findings_works_without_resources_table(tmp_path):
    db_path = tmp_path / "cozo.db"
    module_source = "git::https://example.com/org/terraform-network"
    experiment_id = "exp-456"

    register_module(
        ModuleMetadata(
            module_source=module_source,
            module_name="terraform-network",
            resource_types=["azurerm_virtual_network"],
            outputs={},
            variables={},
            description="test module",
        ),
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT,
            title TEXT,
            description TEXT,
            severity TEXT,
            severity_score INTEGER,
            rule_id TEXT,
            source_file TEXT,
            source_line_start INTEGER,
            code_snippet TEXT,
            attack_impact TEXT,
            category TEXT,
            resource_id INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO findings (
            experiment_id, title, description, severity, severity_score, rule_id,
            source_file, source_line_start, code_snippet, attack_impact, category, resource_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            "Open NSG rule",
            "nsg allows 0.0.0.0/0",
            "high",
            80,
            "azure-nsg-open",
            "main.tf",
            12,
            "cidr_blocks = [\"0.0.0.0/0\"]",
            "remote access",
            "network",
            None,
        ),
    )
    conn.commit()
    conn.close()

    count = capture_module_findings(
        module_source=module_source,
        module_experiment_id=experiment_id,
        db_path=str(db_path),
    )
    stored = get_module_findings(module_source, db_path=str(db_path))

    assert count == 1
    assert len(stored) == 1
    assert stored[0]["resource_type"] is None
    assert stored[0]["resource_name"] is None


def test_capture_module_findings_supports_base_severity_schema(tmp_path):
    db_path = tmp_path / "cozo.db"
    module_source = "git::https://example.com/org/terraform-network"
    experiment_id = "exp-789"

    register_module(
        ModuleMetadata(
            module_source=module_source,
            module_name="terraform-network",
            resource_types=["azurerm_virtual_network"],
            outputs={},
            variables={},
            description="test module",
        ),
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT,
            title TEXT,
            description TEXT,
            base_severity TEXT,
            severity_score INTEGER,
            rule_id TEXT,
            source_file TEXT,
            source_line_start INTEGER,
            category TEXT,
            resource_id INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO findings (
            experiment_id, title, description, base_severity, severity_score, rule_id,
            source_file, source_line_start, category, resource_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_id,
            "Open NSG rule",
            "nsg allows 0.0.0.0/0",
            "high",
            80,
            "azure-nsg-open",
            "main.tf",
            12,
            "network",
            None,
        ),
    )
    conn.commit()
    conn.close()

    count = capture_module_findings(
        module_source=module_source,
        module_experiment_id=experiment_id,
        db_path=str(db_path),
    )
    stored = get_module_findings(module_source, db_path=str(db_path))

    assert count == 1
    assert len(stored) == 1
    assert stored[0]["severity"] == "high"


def test_record_module_usage_supports_base_severity_schema(tmp_path):
    db_path = tmp_path / "cozo.db"
    module_source = "git::https://example.com/org/terraform-network"

    register_module(
        ModuleMetadata(
            module_source=module_source,
            module_name="terraform-network",
            resource_types=["azurerm_virtual_network"],
            outputs={},
            variables={},
            description="test module",
        ),
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT,
            repo_id INTEGER,
            resource_name TEXT,
            resource_type TEXT,
            provider TEXT,
            discovered_by TEXT,
            discovery_method TEXT,
            source_file TEXT,
            source_line_start INTEGER,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT,
            repo_id INTEGER,
            title TEXT,
            description TEXT,
            base_severity TEXT,
            severity_score INTEGER,
            resource_id INTEGER,
            rule_id TEXT,
            source_file TEXT,
            source_line_start INTEGER,
            inherited_from_module TEXT
        )
        """
    )
    conn.execute(
        "UPDATE module_registry SET findings = ? WHERE module_source = ?",
        (
            json.dumps(
                [
                    {
                        "title": "Open NSG rule",
                        "description": "nsg allows 0.0.0.0/0",
                        "severity": "high",
                        "severity_score": 80,
                        "rule_id": "azure-nsg-open",
                        "source_file": "main.tf",
                        "source_line_start": 12,
                        "resource_type": "azurerm_virtual_network",
                    }
                ]
            ),
            module_source,
        ),
    )
    conn.commit()
    conn.close()

    record_module_usage(
        experiment_id="exp-900",
        repo_id=2,
        module_instance_name="network",
        module_source=module_source,
        source_file="main.tf",
        source_line=7,
        resolved_resource_types=["azurerm_virtual_network"],
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT title, base_severity, severity_score, inherited_from_module
        FROM findings
        WHERE repo_id = ?
        """,
        (2,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["title"] == "[Inherited] Open NSG rule"
    assert row["base_severity"] == "high"
    assert row["severity_score"] == 80
    assert row["inherited_from_module"] == module_source
