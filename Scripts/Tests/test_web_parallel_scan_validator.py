#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Validate"))

from web_parallel_scan_validator import (  # noqa: E402
    detect_docs_iac_parity_issues,
    detect_hierarchy_issues,
    detect_missing_connections,
    effective_concurrency,
    RepoOption,
    ScanResult,
    build_retry_metadata,
    filter_repos_to_resolved_intake,
    find_orphan_nodes,
    gather_repo_evidence,
    load_repo_search_roots,
    main,
    merge_final_results,
    parse_args,
    partition_repos,
    resolve_intake_repos,
    run,
    scan_repo_worker,
    should_finish_wait,
    validate_partition_args,
)


def test_find_orphan_nodes_flags_unconnected_nodes():
    code = """
    flowchart LR
      A[api] --> B[db]
      C[cache]
      Internet((internet))
    """
    assert find_orphan_nodes(code) == ["C"]


def test_find_orphan_nodes_ignores_known_placeholder_nodes():
    code = """
    flowchart LR
      Internet((Internet))
      Legend[Legend]
    """
    assert find_orphan_nodes(code) == []


def test_detect_missing_connections_reports_expected_path_gaps():
    code = """
    flowchart LR
      Internet[Internet]
      GW[API Gateway]
      APP[Backend Service]
      DB[(Database)]
    """
    issues = detect_missing_connections(code, provider="azure", diagram_title="Azure diagram")
    kinds = {issue["issue_type"] for issue in issues}
    assert "missing_internet_to_ingress" in kinds
    assert "missing_ingress_to_service" in kinds
    assert "missing_service_to_data" in kinds


def test_find_orphan_nodes_handles_architecture_beta_directional_edges():
    code = """
    architecture-beta
      service internet(logos:globalsign)[Internet]
      service api(logos:aws-api-gateway)[API Gateway]
      service app(logos:aws-lambda)[Backend Service]
      internet:R --> L:api
      api:R --> L:app
    """
    assert find_orphan_nodes(code) == []


def test_detect_docs_iac_parity_issues_flags_missing_expected_asset():
    code = """
    flowchart LR
      APP[Backend Service]
    """
    issues = detect_docs_iac_parity_issues(
        code=code,
        provider="aws",
        diagram_title="AWS diagram",
        expected_assets={"internet_ingress": ["README.md"], "database": ["main.tf"]},
    )
    expected_assets = {issue["expected_asset"] for issue in issues}
    assert "internet_ingress" in expected_assets
    assert "database" in expected_assets


def test_detect_hierarchy_issues_flags_flat_child_resource():
    code = """
    flowchart LR
      storage_acc[Storage Account: data]
      blob_data[Blob: private-data.csv]
      storage_acc --> blob_data
    """
    issues = detect_hierarchy_issues(code=code, provider="azure", diagram_title="Azure diagram")
    assert any(issue.get("issue_type") == "flat_hierarchy_smell" for issue in issues)


def test_gather_repo_evidence_returns_matching_files(tmp_path: Path):
    (tmp_path / "infra.tf").write_text('resource "aws_s3_bucket" "my_bucket" {}', encoding="utf-8")
    (tmp_path / "README.md").write_text("This service references my bucket access path.", encoding="utf-8")
    (tmp_path / "ignore.bin").write_bytes(b"\x00\x01")

    hits = gather_repo_evidence(tmp_path, "my_bucket", max_hits=5)
    assert "README.md" in hits or "infra.tf" in hits


def test_repo_option_api_key_uses_path_basename():
    repo = RepoOption(name="org/repo", path="/home/neil/repos/repo")
    assert repo.api_key == "repo"


def test_should_finish_wait_completed_and_missing_history_paths():
    done, reason = should_finish_wait(None, [{"experiment_id": "123"}], "123")
    assert done is True and reason == "completed"

    done, reason = should_finish_wait(None, [], "123")
    assert done is True and reason == "no_history_record"

    done, reason = should_finish_wait("123", [], "123")
    assert done is False and reason == "running"


def test_resolve_intake_repos_reports_found_and_unresolved(tmp_path: Path):
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    (repos_root / "found-repo").mkdir()

    intake_file = tmp_path / "ReposToScan.txt"
    intake_file.write_text("found-repo\nmissing-repo\n", encoding="utf-8")

    found, unresolved, roots = resolve_intake_repos(intake_file=intake_file, search_roots=[repos_root])

    assert [repo.name for repo in found] == ["found-repo"]
    assert Path(found[0].path) == (repos_root / "found-repo").resolve()
    assert unresolved == [{"name": "missing-repo", "search_roots": roots}]
    assert roots == [str(repos_root)]


def test_filter_repos_to_resolved_intake_keeps_only_resolved(tmp_path: Path):
    included = tmp_path / "included"
    excluded = tmp_path / "excluded"
    included.mkdir()
    excluded.mkdir()

    dropdown = [
        RepoOption(name="included", path=str(included)),
        RepoOption(name="excluded", path=str(excluded)),
    ]
    intake_resolved = [RepoOption(name="included", path=str(included))]

    targeted, skipped = filter_repos_to_resolved_intake(dropdown, intake_resolved)

    assert [repo.name for repo in targeted] == ["included"]
    assert [repo.name for repo in skipped] == ["excluded"]


def test_load_repo_search_roots_uses_paths_json(tmp_path: Path):
    settings = tmp_path / "paths.json"
    settings.write_text('{"repo_search_paths":["~/repo-a","/opt/repo-b"]}', encoding="utf-8")

    roots = load_repo_search_roots(settings)

    assert roots[0] == Path("~/repo-a").expanduser()
    assert roots[1] == Path("/opt/repo-b")


def test_validate_partition_args_accepts_none_or_valid_pair():
    assert validate_partition_args(None, None) == (None, None)
    assert validate_partition_args(4, 2) == (4, 2)


def test_validate_partition_args_rejects_invalid_combinations():
    import pytest

    with pytest.raises(ValueError, match="provided together"):
        validate_partition_args(4, None)
    with pytest.raises(ValueError, match="provided together"):
        validate_partition_args(None, 0)
    with pytest.raises(ValueError, match=">= 1"):
        validate_partition_args(0, 0)
    with pytest.raises(ValueError, match=">= 0"):
        validate_partition_args(2, -1)
    with pytest.raises(ValueError, match="less than"):
        validate_partition_args(2, 2)


def test_partition_repos_deterministic_sharding():
    repos = [
        RepoOption(name="repo-c", path="/z/path"),
        RepoOption(name="repo-a", path="/a/path"),
        RepoOption(name="repo-b", path="/m/path"),
        RepoOption(name="repo-d", path="/x/path"),
    ]

    shard = partition_repos(repos, partition_count=2, partition_index=1)

    assert [(r.name, r.path) for r in shard] == [
        ("repo-b", "/m/path"),
        ("repo-c", "/z/path"),
    ]


def test_parse_args_scan_complete_timeout_default_and_override():
    default_args = parse_args([])
    assert default_args.scan_complete_timeout_sec == 600
    assert default_args.concurrency == 6
    assert default_args.mode == "parallel"

    overridden_args = parse_args(["--scan-complete-timeout-sec", "777"])
    assert overridden_args.scan_complete_timeout_sec == 777


def test_effective_concurrency_enforces_serial_mode():
    assert effective_concurrency("parallel", 6) == 6
    assert effective_concurrency("serial", 6) == 1


def test_retry_metadata_tracks_retry_outcome():
    primary = [
        ScanResult(repo_name="repo-a", repo_path="/repos/repo-a", experiment_id="exp-1", status="failed", error="timeout"),
        ScanResult(repo_name="repo-b", repo_path="/repos/repo-b", experiment_id="exp-2", status="completed"),
    ]
    retry = [
        ScanResult(repo_name="repo-a", repo_path="/repos/repo-a", experiment_id="exp-3", status="completed"),
    ]

    metadata = build_retry_metadata(primary, retry)

    assert metadata == [
        {
            "repo_name": "repo-a",
            "repo_path": "/repos/repo-a",
            "primary_status": "failed",
            "primary_error": "timeout",
            "retry_status": "completed",
            "retry_error": "",
            "retry_passed": True,
        }
    ]


def test_merge_final_results_prefers_retry_result_for_same_repo():
    primary = [
        ScanResult(repo_name="repo-a", repo_path="/repos/repo-a", experiment_id="exp-1", status="failed", error="timeout"),
        ScanResult(repo_name="repo-b", repo_path="/repos/repo-b", experiment_id="exp-2", status="completed"),
    ]
    retry = [
        ScanResult(repo_name="repo-a", repo_path="/repos/repo-a", experiment_id="exp-3", status="completed"),
    ]

    merged = merge_final_results(primary, retry)

    assert [(result.repo_name, result.experiment_id, result.status) for result in merged] == [
        ("repo-a", "exp-3", "completed"),
        ("repo-b", "exp-2", "completed"),
    ]


def test_main_rejects_non_positive_scan_complete_timeout(monkeypatch):
    monkeypatch.setattr(
        "web_parallel_scan_validator.parse_args",
        lambda: parse_args(["--scan-complete-timeout-sec", "0"]),
    )
    assert main() == 2


def test_main_rejects_validate_detection_rules_without_write_flag(monkeypatch):
    monkeypatch.setattr(
        "web_parallel_scan_validator.parse_args",
        lambda: parse_args(["--validate-detection-rules"]),
    )
    assert main() == 2


def test_scan_repo_worker_plumbs_scan_complete_timeout(monkeypatch, tmp_path: Path):
    import asyncio

    class DummyPage:
        pass

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, base_url):
            return DummyContext()

    class DummyLogger:
        async def info(self, _msg):
            return None

        async def warn(self, _msg):
            return None

        async def error(self, _msg):
            return None

        async def improvement(self, _msg):
            return None

    seen: dict[str, int] = {}

    async def fake_wait_for_completion(page, repo_status_key, experiment_id, logger, timeout_sec, repo_display_name=None):
        seen["timeout_sec"] = timeout_sec

    async def fake_start_scan_from_ui(page, repo, logger):
        return None

    async def fake_wait_for_experiment(page, repo_name, logger):
        return "exp-123"

    async def fake_capture_provider_screenshots(*args, **kwargs):
        return []

    async def fake_collect_diagram_validation_issues(*args, **kwargs):
        return ([], [], [], [], [])

    monkeypatch.setattr("web_parallel_scan_validator.start_scan_from_ui", fake_start_scan_from_ui)
    monkeypatch.setattr("web_parallel_scan_validator.wait_for_experiment", fake_wait_for_experiment)
    monkeypatch.setattr("web_parallel_scan_validator.wait_for_completion", fake_wait_for_completion)
    monkeypatch.setattr("web_parallel_scan_validator.capture_provider_screenshots", fake_capture_provider_screenshots)
    monkeypatch.setattr(
        "web_parallel_scan_validator.collect_diagram_validation_issues",
        fake_collect_diagram_validation_issues,
    )

    repo = RepoOption(name="org/repo", path=str(tmp_path))
    result = asyncio.run(
        scan_repo_worker(
            browser=DummyBrowser(),
            base_url="http://127.0.0.1:9000",
            repo=repo,
            screenshots_dir=tmp_path,
            candidate_rules_dir=tmp_path,
            write_rule_candidates=False,
            write_detection_rules_enabled=False,
            validate_detection_rules_enabled=False,
            opengrep_timeout_sec=120,
            scan_complete_timeout_sec=777,
            logger=DummyLogger(),
        )
    )
    assert result.status == "completed"
    assert seen["timeout_sec"] == 777


def test_run_writes_summary_and_finishes_when_worker_hangs(monkeypatch, tmp_path: Path):
    class DummyPage:
        pass

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, base_url):
            return DummyContext()

        async def close(self):
            return None

    class DummyPlaywright:
        class Chromium:
            async def launch(self, **_kwargs):
                return DummyBrowser()

        def __init__(self):
            self.chromium = self.Chromium()

    class DummyPlaywrightContext:
        async def __aenter__(self):
            return DummyPlaywright()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: DummyPlaywrightContext()),
    )
    async def fake_discover_repos(_page):
        return [RepoOption(name="repo-a", path=str(tmp_path / "repo-a"))]

    monkeypatch.setattr("web_parallel_scan_validator.discover_repos", fake_discover_repos)
    monkeypatch.setattr(
        "web_parallel_scan_validator.resolve_intake_repos",
        lambda: (
            [RepoOption(name="repo-a", path=str(tmp_path / "repo-a"))],
            [],
            [str(tmp_path)],
        ),
    )
    monkeypatch.setattr("web_parallel_scan_validator.WORKER_TIMEOUT_GRACE_SEC", 0)

    call_count: list[str] = []
    cancellations: list[str] = []

    async def hanging_worker(**kwargs):
        call_count.append(kwargs["repo"].name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations.append(kwargs["repo"].name)
            raise

    monkeypatch.setattr("web_parallel_scan_validator.scan_repo_worker", hanging_worker)

    args = parse_args(
        [
            "--audit-root",
            str(tmp_path),
            "--scan-complete-timeout-sec",
            "1",
            "--concurrency",
            "1",
        ]
    )
    exit_code = asyncio.run(run(args))

    summary_paths = list(tmp_path.glob("WebScanValidation_*/summary.json"))
    assert exit_code == 1
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    assert summary["repos_total"] == 1
    assert summary["retry_attempted"] == 1
    assert summary["failed"] == 1
    assert summary["completed"] == 0
    assert "worker lifecycle timeout" in summary["results"][0]["error"]
    assert call_count == ["repo-a", "repo-a"]
    assert cancellations == ["repo-a", "repo-a"]


def test_run_serial_mode_processes_repos_in_order(monkeypatch, tmp_path: Path):
    class DummyPage:
        pass

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, base_url):
            return DummyContext()

        async def close(self):
            return None

    class DummyPlaywright:
        class Chromium:
            async def launch(self, **_kwargs):
                return DummyBrowser()

        def __init__(self):
            self.chromium = self.Chromium()

    class DummyPlaywrightContext:
        async def __aenter__(self):
            return DummyPlaywright()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: DummyPlaywrightContext()),
    )

    repos = [
        RepoOption(name="repo-a", path=str(tmp_path / "repo-a")),
        RepoOption(name="repo-b", path=str(tmp_path / "repo-b")),
    ]
    async def fake_discover_repos(_page):
        return repos

    monkeypatch.setattr("web_parallel_scan_validator.discover_repos", fake_discover_repos)
    monkeypatch.setattr(
        "web_parallel_scan_validator.resolve_intake_repos",
        lambda: (repos, [], [str(tmp_path)]),
    )

    call_order: list[str] = []

    async def fake_worker(**kwargs):
        repo = kwargs["repo"]
        call_order.append(repo.name)
        return ScanResult(
            repo_name=repo.name,
            repo_path=repo.path,
            experiment_id=f"exp-{repo.name}",
            status="completed",
            provider_screenshots=[],
            orphan_issues=[],
            connection_issues=[],
            parity_issues=[],
            hierarchy_issues=[],
            rule_candidates=[],
        )

    monkeypatch.setattr("web_parallel_scan_validator.scan_repo_worker", fake_worker)

    args = parse_args(
        [
            "--audit-root",
            str(tmp_path),
            "--mode",
            "serial",
            "--concurrency",
            "6",
        ]
    )
    exit_code = asyncio.run(run(args))

    summary_paths = list(tmp_path.glob("WebScanValidation_*/summary.json"))
    assert exit_code == 0
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    assert summary["mode"] == "serial"
    assert summary["effective_concurrency"] == 1
    assert summary["completed"] == 2
    assert call_order == ["repo-a", "repo-b"]


def test_run_repo_at_a_time_retries_before_next_repo(monkeypatch, tmp_path: Path):
    class DummyPage:
        pass

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, base_url):
            return DummyContext()

        async def close(self):
            return None

    class DummyPlaywright:
        class Chromium:
            async def launch(self, **_kwargs):
                return DummyBrowser()

        def __init__(self):
            self.chromium = self.Chromium()

    class DummyPlaywrightContext:
        async def __aenter__(self):
            return DummyPlaywright()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: DummyPlaywrightContext()),
    )

    repos = [
        RepoOption(name="repo-a", path=str(tmp_path / "repo-a")),
        RepoOption(name="repo-b", path=str(tmp_path / "repo-b")),
    ]

    async def fake_discover_repos(_page):
        return repos

    monkeypatch.setattr("web_parallel_scan_validator.discover_repos", fake_discover_repos)
    monkeypatch.setattr(
        "web_parallel_scan_validator.resolve_intake_repos",
        lambda: (repos, [], [str(tmp_path)]),
    )

    attempts_by_repo: dict[str, int] = {}
    call_order: list[str] = []

    async def fake_worker(**kwargs):
        repo = kwargs["repo"]
        attempts_by_repo[repo.name] = attempts_by_repo.get(repo.name, 0) + 1
        call_order.append(f"{repo.name}#{attempts_by_repo[repo.name]}")
        first_attempt_fails = repo.name == "repo-a" and attempts_by_repo[repo.name] == 1
        return ScanResult(
            repo_name=repo.name,
            repo_path=repo.path,
            experiment_id=f"exp-{repo.name}-{attempts_by_repo[repo.name]}",
            status="failed" if first_attempt_fails else "completed",
            error="boom" if first_attempt_fails else "",
            provider_screenshots=[],
            orphan_issues=[],
            connection_issues=[],
            parity_issues=[],
            hierarchy_issues=[],
            resource_value_assessments=[],
            rule_candidates=[],
            detection_rules=[],
            rule_validations=[],
        )

    monkeypatch.setattr("web_parallel_scan_validator.scan_repo_worker", fake_worker)

    args = parse_args(
        [
            "--audit-root",
            str(tmp_path),
            "--repo-at-a-time",
            "--concurrency",
            "6",
        ]
    )
    exit_code = asyncio.run(run(args))

    summary_paths = list(tmp_path.glob("WebScanValidation_*/summary.json"))
    assert exit_code == 0
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    assert summary["repo_at_a_time"] is True
    assert summary["effective_concurrency"] == 1
    assert summary["retry_attempted"] == 1
    assert summary["retry_recovered"] == 1
    assert call_order == ["repo-a#1", "repo-a#2", "repo-b#1"]
