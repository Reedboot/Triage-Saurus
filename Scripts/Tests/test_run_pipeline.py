#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Utils"))

import run_pipeline  # noqa: E402


def test_run_parallel_tasks_returns_phase_statuses(monkeypatch):
    calls: list[tuple[list[str], str, int | None, bool]] = []

    def fake_run(cmd, label, timeout=None, capture_output=False):
        calls.append((cmd, label, timeout, capture_output))
        return 0 if "task-ok" in label else 7

    monkeypatch.setattr(run_pipeline, "_run", fake_run)

    results = run_pipeline._run_parallel_tasks(
        [
            ("3c.1", ["python", "a.py"], "task-ok"),
            ("3c.2", ["python", "b.py"], "task-fail"),
        ]
    )

    assert results == {"3c.1": 0, "3c.2": 7}
    assert sorted(calls) == sorted(
        [
            (["python", "a.py"], "task-ok", None, True),
            (["python", "b.py"], "task-fail", None, True),
        ]
    )


def test_run_phase3c_runs_gated_tasks_when_dependencies_pass(monkeypatch):
    run_calls: list[str] = []
    phase_ids: list[str] = []
    fixed: list[str] = []

    def fake_run(cmd, label, timeout=None, capture_output=False):
        run_calls.append(label)
        return 0

    def fake_parallel(tasks):
        phase_ids.extend([phase for phase, _, _ in tasks])
        return {"3c.1": 0, "3c.2": 0, "3c.2b": 0, "3c.3": 0}

    def fake_fix(experiment_id):
        fixed.append(experiment_id)
        return {"docker_fixed": 1, "kubernetes_fixed": 2, "errors": []}

    monkeypatch.setattr(run_pipeline, "_run", fake_run)
    monkeypatch.setattr(run_pipeline, "_run_parallel_tasks", fake_parallel)
    monkeypatch.setattr(run_pipeline, "fix_nested_resource_providers", fake_fix)

    run_pipeline._run_phase3c("123", Path("/repo"))

    assert phase_ids == ["3c.1", "3c.2", "3c.2b", "3c.3"]
    assert run_calls == [
        "Phase 3c — Infer semantic connections and data flows",
        "Phase 3c.4 — Link CI/CD artifacts to IaC deployment targets",
        "Phase 3c.5 — Analyze CI/CD artifacts for security vulnerabilities",
    ]
    assert fixed == ["123"]


def test_run_phase3c_skips_dependent_tasks_when_3c3_fails(monkeypatch):
    run_calls: list[str] = []
    fixed: list[str] = []

    def fake_run(cmd, label, timeout=None, capture_output=False):
        run_calls.append(label)
        return 0

    def fake_parallel(_tasks):
        return {"3c.1": 0, "3c.2": 0, "3c.2b": 0, "3c.3": 9}

    def fake_fix(experiment_id):
        fixed.append(experiment_id)
        return {"docker_fixed": 0, "kubernetes_fixed": 0, "errors": []}

    monkeypatch.setattr(run_pipeline, "_run", fake_run)
    monkeypatch.setattr(run_pipeline, "_run_parallel_tasks", fake_parallel)
    monkeypatch.setattr(run_pipeline, "fix_nested_resource_providers", fake_fix)

    run_pipeline._run_phase3c("999", Path("/repo"))

    assert run_calls == ["Phase 3c — Infer semantic connections and data flows"]
    assert fixed == ["999"]


def test_run_phase3c_runs_sequential_when_parallel_flag_disabled(monkeypatch):
    run_calls: list[str] = []
    fixed: list[str] = []

    def fake_run(cmd, label, timeout=None, capture_output=False):
        run_calls.append(label)
        return 0

    def fail_parallel(_tasks):
        raise AssertionError("parallel runner should not be used when flag is disabled")

    def fake_fix(experiment_id):
        fixed.append(experiment_id)
        return {"docker_fixed": 0, "kubernetes_fixed": 0, "errors": []}

    monkeypatch.setenv("TRIAGE_PIPELINE_PARALLEL_MODE", "0")
    monkeypatch.setattr(run_pipeline, "_run", fake_run)
    monkeypatch.setattr(run_pipeline, "_run_parallel_tasks", fail_parallel)
    monkeypatch.setattr(run_pipeline, "fix_nested_resource_providers", fake_fix)

    run_pipeline._run_phase3c("seq1", Path("/repo"))

    assert run_calls == [
        "Phase 3c — Infer semantic connections and data flows",
        "Phase 3c.1 — Extract SG rules and create Internet exposure connections",
        "Phase 3c.2 — Extract container definitions from user_data",
        "Phase 3c.2b — Extract container definitions from K8s manifests",
        "Phase 3c.3 — Extract CI/CD artifacts and deployment targets",
        "Phase 3c.4 — Link CI/CD artifacts to IaC deployment targets",
        "Phase 3c.5 — Analyze CI/CD artifacts for security vulnerabilities",
    ]
    assert fixed == ["seq1"]
