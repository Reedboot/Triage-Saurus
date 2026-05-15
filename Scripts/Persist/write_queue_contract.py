#!/usr/bin/env python3
"""Queue contract for single-writer persistence operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Optional


CONTRACT_VERSION = "queue-contract/v1"
_ALLOWED_OWNER_TYPES = {"context_discovery", "scan_pipeline", "manual", "system"}
_IDEMPOTENCY_PATTERN = re.compile(r"^tsq:v1:[a-z_]+:[a-f0-9]{24}$")


class OperationKind(str, Enum):
    RESOURCE_UPSERT = "resource_upsert"
    CONNECTION_UPSERT = "connection_upsert"
    METADATA_UPSERT = "metadata_upsert"


@dataclass(frozen=True)
class OperationOwner:
    """Ownership scope for enforcing single-writer partitioning."""

    owner_type: str
    owner_id: str
    experiment_id: str
    repo_name: Optional[str] = None

    def __post_init__(self) -> None:
        owner_type = self.owner_type.strip().lower()
        owner_id = self.owner_id.strip()
        experiment_id = self.experiment_id.strip()
        repo_name = self.repo_name.strip() if self.repo_name else None

        if owner_type not in _ALLOWED_OWNER_TYPES:
            valid = ", ".join(sorted(_ALLOWED_OWNER_TYPES))
            raise ValueError(f"owner_type must be one of: {valid}")
        if not owner_id:
            raise ValueError("owner_id is required")
        if not experiment_id:
            raise ValueError("experiment_id is required")

        object.__setattr__(self, "owner_type", owner_type)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "repo_name", repo_name)

    @property
    def partition_key(self) -> str:
        repo_part = self.repo_name or "global"
        return f"{self.experiment_id}:{repo_part}"


@dataclass(frozen=True)
class ResourceUpsert:
    resource_type: str
    terraform_name: str
    source_repo: str
    canonical_name: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_type.strip():
            raise ValueError("resource_type is required")
        if not self.terraform_name.strip():
            raise ValueError("terraform_name is required")
        if not self.source_repo.strip():
            raise ValueError("source_repo is required")

    def as_payload(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "terraform_name": self.terraform_name,
            "source_repo": self.source_repo,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class ConnectionUpsert:
    experiment_id: str
    source_resource_id: int
    connection_type: str
    target_resource_id: Optional[int] = None
    target_external: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[str] = None
    connection_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if self.source_resource_id <= 0:
            raise ValueError("source_resource_id must be > 0")
        if not self.connection_type.strip():
            raise ValueError("connection_type is required")
        if self.target_resource_id is None and not (self.target_external or "").strip():
            raise ValueError("connection upsert requires target_resource_id or target_external")

    def as_payload(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "source_resource_id": self.source_resource_id,
            "target_resource_id": self.target_resource_id,
            "target_external": self.target_external,
            "connection_type": self.connection_type,
            "protocol": self.protocol,
            "port": self.port,
            "connection_metadata": dict(self.connection_metadata),
        }


@dataclass(frozen=True)
class MetadataUpsert:
    experiment_id: str
    namespace: str
    key: str
    value: str
    source: str
    repo_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if not self.namespace.strip():
            raise ValueError("namespace is required")
        if not self.key.strip():
            raise ValueError("key is required")
        if self.value is None:
            raise ValueError("value is required")
        if not self.source.strip():
            raise ValueError("source is required")

    def as_payload(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "repo_name": self.repo_name,
            "namespace": self.namespace,
            "key": self.key,
            "value": self.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not _IDEMPOTENCY_PATTERN.match(self.value):
            raise ValueError("Invalid idempotency key format")

    @classmethod
    def from_components(
        cls,
        *,
        kind: OperationKind,
        owner: OperationOwner,
        payload: Mapping[str, Any],
    ) -> "IdempotencyKey":
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        raw = f"{CONTRACT_VERSION}|{kind.value}|{owner.partition_key}|{owner.owner_type}|{owner.owner_id}|{canonical_payload}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return cls(f"tsq:v1:{kind.value}:{digest}")


@dataclass(frozen=True)
class WriteOperation:
    kind: OperationKind
    owner: OperationOwner
    payload: Mapping[str, Any]
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        normalized_payload = _normalized_payload(self.kind, self.payload)
        _validate_ownership(self.kind, self.owner, normalized_payload)
        object.__setattr__(self, "payload", normalized_payload)

    @property
    def partition_key(self) -> str:
        return self.owner.partition_key

    def as_message(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "partition_key": self.partition_key,
            "kind": self.kind.value,
            "idempotency_key": self.idempotency_key.value,
            "owner": {
                "owner_type": self.owner.owner_type,
                "owner_id": self.owner.owner_id,
                "experiment_id": self.owner.experiment_id,
                "repo_name": self.owner.repo_name,
            },
            "payload": dict(self.payload),
        }


def build_write_operation(
    *,
    kind: OperationKind,
    owner: OperationOwner,
    payload: Mapping[str, Any],
) -> WriteOperation:
    normalized_payload = _normalized_payload(kind, payload)
    _validate_ownership(kind, owner, normalized_payload)
    key = IdempotencyKey.from_components(kind=kind, owner=owner, payload=normalized_payload)
    return WriteOperation(
        kind=kind,
        owner=owner,
        payload=normalized_payload,
        idempotency_key=key,
    )


def _normalized_payload(kind: OperationKind, payload: Mapping[str, Any]) -> dict[str, Any]:
    if kind is OperationKind.RESOURCE_UPSERT:
        return ResourceUpsert(**dict(payload)).as_payload()
    if kind is OperationKind.CONNECTION_UPSERT:
        return ConnectionUpsert(**dict(payload)).as_payload()
    if kind is OperationKind.METADATA_UPSERT:
        return MetadataUpsert(**dict(payload)).as_payload()
    raise ValueError(f"Unsupported operation kind: {kind}")


def _validate_ownership(kind: OperationKind, owner: OperationOwner, payload: Mapping[str, Any]) -> None:
    payload_experiment = str(payload.get("experiment_id") or "").strip()
    payload_repo = str(payload.get("source_repo") or payload.get("repo_name") or "").strip()

    if kind is OperationKind.RESOURCE_UPSERT:
        if not owner.repo_name:
            raise ValueError("resource_upsert requires owner.repo_name")
        if payload_repo != owner.repo_name:
            raise ValueError("resource_upsert source_repo must match owner.repo_name")
        return

    if payload_experiment != owner.experiment_id:
        raise ValueError("operation experiment_id must match owner.experiment_id")

    if owner.repo_name and payload_repo and payload_repo != owner.repo_name:
        raise ValueError("operation repo ownership mismatch")
