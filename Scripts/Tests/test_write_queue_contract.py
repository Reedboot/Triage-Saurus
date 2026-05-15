#!/usr/bin/env python3

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Persist"))

from write_queue_contract import (  # noqa: E402
    OperationKind,
    OperationOwner,
    build_write_operation,
)


def test_resource_upsert_contract_generates_stable_idempotency_key():
    owner = OperationOwner(
        owner_type="scan_pipeline",
        owner_id="run-123",
        experiment_id="exp-1",
        repo_name="RepoA",
    )
    payload = {
        "resource_type": "azurerm_storage_account",
        "terraform_name": "sa_main",
        "source_repo": "RepoA",
        "aliases": ["RepoA", "RepoA-alt"],
        "properties": {"tier": "Standard"},
    }

    first = build_write_operation(kind=OperationKind.RESOURCE_UPSERT, owner=owner, payload=payload)
    second = build_write_operation(kind=OperationKind.RESOURCE_UPSERT, owner=owner, payload=payload)

    assert first.idempotency_key.value == second.idempotency_key.value
    assert first.partition_key == "exp-1:RepoA"
    assert first.as_message()["contract_version"] == "queue-contract/v1"


def test_resource_upsert_ownership_mismatch_is_rejected():
    owner = OperationOwner(
        owner_type="context_discovery",
        owner_id="ctx-01",
        experiment_id="exp-1",
        repo_name="RepoA",
    )

    with pytest.raises(ValueError, match="source_repo must match owner.repo_name"):
        build_write_operation(
            kind=OperationKind.RESOURCE_UPSERT,
            owner=owner,
            payload={
                "resource_type": "azurerm_key_vault",
                "terraform_name": "kv1",
                "source_repo": "RepoB",
            },
        )


def test_connection_upsert_requires_target_pointer():
    owner = OperationOwner(
        owner_type="scan_pipeline",
        owner_id="run-123",
        experiment_id="exp-1",
        repo_name="RepoA",
    )

    with pytest.raises(ValueError, match="requires target_resource_id or target_external"):
        build_write_operation(
            kind=OperationKind.CONNECTION_UPSERT,
            owner=owner,
            payload={
                "experiment_id": "exp-1",
                "source_resource_id": 41,
                "connection_type": "depends_on",
            },
        )


def test_metadata_upsert_experiment_ownership_is_enforced():
    owner = OperationOwner(
        owner_type="manual",
        owner_id="cli-user",
        experiment_id="exp-owner",
        repo_name="RepoA",
    )

    with pytest.raises(ValueError, match="experiment_id must match owner.experiment_id"):
        build_write_operation(
            kind=OperationKind.METADATA_UPSERT,
            owner=owner,
            payload={
                "experiment_id": "exp-other",
                "repo_name": "RepoA",
                "namespace": "phase2",
                "key": "ingress_paths",
                "value": "[]",
                "source": "context_discovery",
            },
        )


def test_metadata_upsert_accepts_repo_scoped_payload():
    owner = OperationOwner(
        owner_type="system",
        owner_id="scheduler",
        experiment_id="exp-2",
        repo_name="RepoX",
    )

    operation = build_write_operation(
        kind=OperationKind.METADATA_UPSERT,
        owner=owner,
        payload={
            "experiment_id": "exp-2",
            "repo_name": "RepoX",
            "namespace": "phase2",
            "key": "auth_model",
            "value": "managed_identity",
            "source": "knowledge_agent",
        },
    )

    assert operation.partition_key == "exp-2:RepoX"
    assert operation.payload["key"] == "auth_model"
