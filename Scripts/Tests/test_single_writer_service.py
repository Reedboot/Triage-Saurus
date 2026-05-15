#!/usr/bin/env python3

from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

from single_writer_service import SingleWriterService  # noqa: E402
from write_queue_contract import OperationKind, OperationOwner, build_write_operation  # noqa: E402


def _test_db_path(name: str) -> Path:
    return ROOT / "Output" / "Data" / f"test_single_writer_service_{name}.sqlite"


def _reset_test_db(path: Path) -> None:
    if path.exists():
        path.unlink()


def test_writer_handles_batching_and_idempotency_for_metadata():
    db_path = _test_db_path("metadata")
    _reset_test_db(db_path)

    owner = OperationOwner(
        owner_type="scan_pipeline",
        owner_id="run-1",
        experiment_id="exp-batch",
        repo_name="RepoA",
    )
    op = build_write_operation(
        kind=OperationKind.METADATA_UPSERT,
        owner=owner,
        payload={
            "experiment_id": "exp-batch",
            "repo_name": "RepoA",
            "namespace": "scan",
            "key": "languages_detected",
            "value": "Python, Terraform",
            "source": "test",
        },
    )

    writer = SingleWriterService(batch_size=2, db_path=db_path)
    writer.submit(operation=op)
    writer.submit(operation=op)
    result = writer.flush()

    assert result.received == 2
    assert result.persisted == 1
    assert result.duplicates == 1

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT cm.value
            FROM context_metadata cm
            JOIN repositories r ON cm.repo_id = r.id
            WHERE cm.experiment_id = ? AND r.repo_name = ? AND cm.namespace = ? AND cm.key = ?
            """,
            ("exp-batch", "RepoA", "scan", "languages_detected"),
        ).fetchone()
        assert row is not None
        assert row[0] == "Python, Terraform"

    _reset_test_db(db_path)


def test_writer_resource_and_connection_upserts_are_idempotent():
    db_path = _test_db_path("resource_connection")
    _reset_test_db(db_path)

    owner = OperationOwner(
        owner_type="context_discovery",
        owner_id="ctx-1",
        experiment_id="exp-resource",
        repo_name="RepoGraph",
    )
    writer = SingleWriterService(batch_size=10, db_path=db_path)

    source_op = build_write_operation(
        kind=OperationKind.RESOURCE_UPSERT,
        owner=owner,
        payload={
            "resource_type": "azurerm_storage_account",
            "terraform_name": "source_sa",
            "source_repo": "RepoGraph",
            "aliases": ["source_sa"],
            "properties": {"sku": "Standard_LRS"},
        },
    )
    target_op = build_write_operation(
        kind=OperationKind.RESOURCE_UPSERT,
        owner=owner,
        payload={
            "resource_type": "azurerm_key_vault",
            "terraform_name": "target_kv",
            "source_repo": "RepoGraph",
            "aliases": ["target_kv"],
            "properties": {"rbac_authorization_enabled": True},
        },
    )
    writer.submit(operation=source_op)
    writer.submit(operation=target_op)
    writer.flush()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO repositories (experiment_id, repo_name) VALUES (?, ?)",
            ("exp-resource", "RepoGraph"),
        )
        repo_id = conn.execute(
            "SELECT id FROM repositories WHERE experiment_id = ? AND repo_name = ?",
            ("exp-resource", "RepoGraph"),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO resources (experiment_id, repo_id, resource_name, resource_type)
            VALUES (?, ?, ?, ?), (?, ?, ?, ?)
            """,
            (
                "exp-resource",
                repo_id,
                "source-sa-runtime",
                "azurerm_storage_account",
                "exp-resource",
                repo_id,
                "target-kv-runtime",
                "azurerm_key_vault",
            ),
        )
        src_resource_id = conn.execute(
            "SELECT id FROM resources WHERE experiment_id = ? AND resource_name = ?",
            ("exp-resource", "source-sa-runtime"),
        ).fetchone()[0]
        tgt_resource_id = conn.execute(
            "SELECT id FROM resources WHERE experiment_id = ? AND resource_name = ?",
            ("exp-resource", "target-kv-runtime"),
        ).fetchone()[0]

    conn_owner = OperationOwner(
        owner_type="scan_pipeline",
        owner_id="run-conn-1",
        experiment_id="exp-resource",
        repo_name="RepoGraph",
    )
    connection_op = build_write_operation(
        kind=OperationKind.CONNECTION_UPSERT,
        owner=conn_owner,
        payload={
            "experiment_id": "exp-resource",
            "source_resource_id": src_resource_id,
            "target_resource_id": tgt_resource_id,
            "connection_type": "depends_on",
            "protocol": "https",
            "connection_metadata": {"path": "integration-test"},
        },
    )
    writer.submit(operation=connection_op)
    writer.submit(operation=connection_op)
    result = writer.flush()

    assert result.persisted == 1
    assert result.duplicates == 1

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM resource_connections
            WHERE experiment_id = ? AND source_resource_id = ? AND target_resource_id = ? AND connection_type = ?
            """,
            ("exp-resource", src_resource_id, tgt_resource_id, "depends_on"),
        ).fetchone()
        assert row[0] == 1

    _reset_test_db(db_path)
