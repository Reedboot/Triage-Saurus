"""Shared staged harvest result helpers."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any


AssetRow = dict[str, Any]


@dataclass
class BackfillJob:
    label: str
    future: Future[Any]


@dataclass
class StagedRows:
    core_rows: list[AssetRow]
    backfill_jobs: list[BackfillJob] = field(default_factory=list)
