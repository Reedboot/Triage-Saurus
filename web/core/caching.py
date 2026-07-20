from __future__ import annotations

import contextvars
import re
import threading
import time
from pathlib import Path

_REPO_ROOT: Path | None = None

_DOCKERFILE_CACHE: dict[str, tuple[int, int, tuple[tuple[str, int | None], ...]]] = {}
_DOCKERFILE_CACHE_MAX = 512
_RESOLVED_REPOS_CACHE: dict[str, object] = {"sig": None, "entries": []}
_AI_ANALYSIS_JOBS: dict[str, dict] = {}
_AI_ANALYSIS_LOCK = threading.Lock()
_ACTIVE_AI_JOB_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar("ACTIVE_AI_JOB_KEY", default=None)


def configure_repo_root(repo_root: Path) -> None:
    global _REPO_ROOT
    _REPO_ROOT = repo_root


def _ai_job_key(experiment_id: str, repo_name: str) -> str:
    return f"{experiment_id}:{repo_name.lower()}"


def _ai_raw_output_file(key: str) -> Path:
    if _REPO_ROOT is None:
        raise RuntimeError("Repo root not configured")
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return _REPO_ROOT / "Output" / "AILogs" / f"{safe_key}-raw.txt"


def _touch_ai_job_activity(key: str) -> None:
    with _AI_ANALYSIS_LOCK:
        job = _AI_ANALYSIS_JOBS.get(key)
        if not job:
            return
        job["last_activity_at"] = time.time()
        _AI_ANALYSIS_JOBS[key] = job

