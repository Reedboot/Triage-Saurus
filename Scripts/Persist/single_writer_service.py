#!/usr/bin/env python3
"""Single-writer queue consumer for contract-backed persistence operations."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
from pathlib import Path
from typing import Iterable, Optional

try:
    from . import db_helpers
    from .write_queue_contract import (
        CONTRACT_VERSION,
        OperationKind,
        OperationOwner,
        WriteOperation,
        build_write_operation,
    )
except ImportError:
    import db_helpers  # type: ignore
    from write_queue_contract import (  # type: ignore
        CONTRACT_VERSION,
        OperationKind,
        OperationOwner,
        WriteOperation,
        build_write_operation,
    )


@dataclass
class WriteBatchResult:
    received: int = 0
    persisted: int = 0
    duplicates: int = 0


class SingleWriterService:
    """Thread-safe queue API with idempotent, batched sqlite writes."""

    def __init__(self, *, batch_size: int = 100, db_path: Optional[Path] = None):
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self._batch_size = batch_size
        self._db_path = db_path
        self._queue: deque[WriteOperation] = deque()
        self._queue_lock = threading.RLock()
        self._flush_lock = threading.Lock()
        self._closed = False

    def submit(
        self,
        *,
        operation: Optional[WriteOperation] = None,
        kind: Optional[OperationKind] = None,
        owner: Optional[OperationOwner] = None,
        payload: Optional[dict] = None,
    ) -> WriteOperation:
        if self._closed:
            raise RuntimeError("SingleWriterService is closed")

        if operation is None:
            if kind is None or owner is None or payload is None:
                raise ValueError("submit requires operation or kind/owner/payload")
            operation = build_write_operation(kind=kind, owner=owner, payload=payload)

        with self._queue_lock:
            self._queue.append(operation)
        return operation

    def submit_many(self, operations: Iterable[WriteOperation]) -> int:
        count = 0
        with self._queue_lock:
            if self._closed:
                raise RuntimeError("SingleWriterService is closed")
            for operation in operations:
                self._queue.append(operation)
                count += 1
        return count

    def submit_message(self, message: dict) -> WriteOperation:
        if message.get("contract_version") != CONTRACT_VERSION:
            raise ValueError("Unsupported contract_version")

        owner_data = message.get("owner") or {}
        owner = OperationOwner(
            owner_type=str(owner_data.get("owner_type", "")),
            owner_id=str(owner_data.get("owner_id", "")),
            experiment_id=str(owner_data.get("experiment_id", "")),
            repo_name=owner_data.get("repo_name"),
        )
        kind = OperationKind(str(message.get("kind", "")))
        payload = dict(message.get("payload") or {})
        operation = build_write_operation(kind=kind, owner=owner, payload=payload)
        if operation.idempotency_key.value != str(message.get("idempotency_key", "")):
            raise ValueError("message idempotency_key does not match contract payload")
        return self.submit(operation=operation)

    def flush(self) -> WriteBatchResult:
        result = WriteBatchResult()
        while True:
            batch = self._pop_batch(self._batch_size)
            if not batch:
                return result

            result.received += len(batch)
            with self._flush_lock:
                with db_helpers.get_db_connection(self._db_path) as conn:
                    for operation in batch:
                        if not _record_operation_once(conn, operation):
                            result.duplicates += 1
                            continue
                        _apply_operation(conn, operation)
                        result.persisted += 1

    def close(self) -> WriteBatchResult:
        self._closed = True
        return self.flush()

    def _pop_batch(self, size: int) -> list[WriteOperation]:
        with self._queue_lock:
            if not self._queue:
                return []
            items: list[WriteOperation] = []
            while self._queue and len(items) < size:
                items.append(self._queue.popleft())
            return items


def _record_operation_once(conn, operation: WriteOperation) -> bool:
    cursor = conn.execute(
        """
        INSERT INTO write_operation_log
        (idempotency_key, partition_key, operation_kind, owner_type, owner_id, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        RETURNING id
        """,
        (
            operation.idempotency_key.value,
            operation.partition_key,
            operation.kind.value,
            operation.owner.owner_type,
            operation.owner.owner_id,
            json.dumps(operation.payload, sort_keys=True),
        ),
    )
    return cursor.fetchone() is not None


def _apply_operation(conn, operation: WriteOperation) -> None:
    if operation.kind is OperationKind.RESOURCE_UPSERT:
        _apply_resource_upsert(conn, operation)
        return
    if operation.kind is OperationKind.CONNECTION_UPSERT:
        _apply_connection_upsert(conn, operation)
        return
    if operation.kind is OperationKind.METADATA_UPSERT:
        payload = operation.payload
        db_helpers.upsert_context_metadata_tx(
            conn,
            experiment_id=payload["experiment_id"],
            repo_name=payload.get("repo_name") or operation.owner.repo_name or "unknown",
            namespace=payload["namespace"],
            key=payload["key"],
            value=payload["value"],
            source=payload["source"],
        )
        return
    raise ValueError(f"Unsupported operation kind: {operation.kind}")


def _apply_resource_upsert(conn, operation: WriteOperation) -> int:
    payload = operation.payload
    aliases = payload.get("aliases") or []
    canonical_name = payload.get("canonical_name") or ""
    terraform_name = payload["terraform_name"]
    source_repo = payload["source_repo"]
    provider = payload["resource_type"].split("_", 1)[0] if "_" in payload["resource_type"] else None
    properties = payload.get("properties") or {}

    cursor = conn.execute(
        """
        INSERT INTO resource_nodes
        (resource_type, terraform_name, canonical_name, friendly_name, display_label,
         provider, source_repo, aliases, confidence, properties, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'extracted', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(resource_type, terraform_name, source_repo) DO UPDATE SET
          canonical_name = CASE
              WHEN excluded.canonical_name IS NOT NULL AND TRIM(excluded.canonical_name) != ''
              THEN excluded.canonical_name
              ELSE resource_nodes.canonical_name
          END,
          aliases = excluded.aliases,
          provider = COALESCE(excluded.provider, resource_nodes.provider),
          properties = excluded.properties,
          updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (
            payload["resource_type"],
            terraform_name,
            canonical_name,
            canonical_name or terraform_name,
            canonical_name or terraform_name,
            provider,
            source_repo,
            json.dumps(sorted(set(aliases))),
            json.dumps(properties, sort_keys=True),
        ),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("resource upsert did not return an id")
    return int(row[0])


def _apply_connection_upsert(conn, operation: WriteOperation) -> Optional[int]:
    payload = operation.payload
    source_resource_id = int(payload["source_resource_id"])
    target_resource_id = payload.get("target_resource_id")
    target_external = payload.get("target_external")
    connection_type = payload.get("connection_type")

    source_repo_row = conn.execute(
        "SELECT repo_id FROM resources WHERE id = ? LIMIT 1",
        (source_resource_id,),
    ).fetchone()
    source_repo_id = source_repo_row[0] if source_repo_row else None

    target_repo_id = None
    if target_resource_id is not None:
        target_repo_row = conn.execute(
            "SELECT repo_id FROM resources WHERE id = ? LIMIT 1",
            (target_resource_id,),
        ).fetchone()
        target_repo_id = target_repo_row[0] if target_repo_row else None

    is_cross_repo = int(bool(target_resource_id is None or (source_repo_id and target_repo_id and source_repo_id != target_repo_id)))
    metadata_json = json.dumps(payload.get("connection_metadata") or {}, sort_keys=True)

    existing = conn.execute(
        """
        SELECT id FROM resource_connections
        WHERE experiment_id = ?
          AND source_resource_id = ?
          AND COALESCE(target_resource_id, -1) = COALESCE(?, -1)
          AND COALESCE(target_external, '') = COALESCE(?, '')
          AND COALESCE(connection_type, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (
            payload["experiment_id"],
            source_resource_id,
            target_resource_id,
            target_external,
            connection_type,
        ),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE resource_connections
            SET source_repo_id = ?,
                target_repo_id = COALESCE(?, target_repo_id),
                is_cross_repo = ?,
                protocol = COALESCE(?, protocol),
                port = COALESCE(?, port),
                target_external = COALESCE(?, target_external),
                connection_metadata = COALESCE(?, connection_metadata)
            WHERE id = ?
            """,
            (
                source_repo_id,
                target_repo_id,
                is_cross_repo,
                payload.get("protocol"),
                payload.get("port"),
                target_external,
                metadata_json,
                existing[0],
            ),
        )
        return int(existing[0])

    cursor = conn.execute(
        """
        INSERT INTO resource_connections
        (experiment_id, source_resource_id, target_resource_id, source_repo_id, target_repo_id,
         is_cross_repo, connection_type, protocol, port, target_external, connection_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            payload["experiment_id"],
            source_resource_id,
            target_resource_id,
            source_repo_id,
            target_repo_id,
            is_cross_repo,
            connection_type,
            payload.get("protocol"),
            payload.get("port"),
            target_external,
            metadata_json,
        ),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None
