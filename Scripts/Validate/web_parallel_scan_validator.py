#!/usr/bin/env python3
"""Run headless web scans in parallel and validate rendered cloud diagrams.

Workflow:
1. Open the web UI and read all repositories from the dropdown.
2. Start scans with bounded concurrency (default: 6).
3. Process results as each repo completes (timeouts fail fast and move on).
4. Retry failed repositories once after the primary pass.
5. Capture provider-tab screenshots for each completed experiment.
6. Detect disconnected/orphan nodes in Mermaid diagrams.
7. Enrich orphan findings with repo evidence hints and provider-default references.

This script is intentionally audit-heavy: it writes progress to stdout and to a
timestamped run log under Output/Audit/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:9000"
DEFAULT_AUDIT_ROOT = REPO_ROOT / "Output" / "Audit"
SETTINGS_PATH = REPO_ROOT / "Settings" / "paths.json"
INTAKE_REPOS_FILE = REPO_ROOT / "Intake" / "ReposToScan.txt"

SCAN_START_TIMEOUT_SEC = 90
DEFAULT_SCAN_COMPLETE_TIMEOUT_SEC = 600
POLL_INTERVAL_SEC = 5
WORKER_TIMEOUT_GRACE_SEC = 90

IGNORED_ORPHAN_NODE_IDS = {
    "internet",
    "legend",
    "notes",
    "note",
    "external",
    "unknown",
}

EVIDENCE_SUFFIXES = {
    ".tf",
    ".tfvars",
    ".hcl",
    ".yml",
    ".yaml",
    ".json",
    ".md",
    ".txt",
    ".py",
    ".cs",
    ".go",
    ".js",
    ".ts",
    ".java",
}

PROVIDER_DEFAULT_REFERENCES: dict[str, list[dict[str, str]]] = {
    "azure": [
        {
            "topic": "Storage account network access defaults",
            "url": "https://learn.microsoft.com/azure/storage/common/storage-network-security",
        },
        {
            "topic": "Azure API Management network modes",
            "url": "https://learn.microsoft.com/azure/api-management/virtual-network-concepts",
        },
    ],
    "aws": [
        {
            "topic": "S3 Block Public Access default behavior",
            "url": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html",
        },
        {
            "topic": "Security group inbound/outbound defaults",
            "url": "https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html",
        },
    ],
    "gcp": [
        {
            "topic": "Cloud Storage public access and IAM defaults",
            "url": "https://cloud.google.com/storage/docs/access-control",
        },
        {
            "topic": "Cloud SQL network access configuration",
            "url": "https://cloud.google.com/sql/docs/mysql/configure-ip",
        },
    ],
    "kubernetes": [
        {
            "topic": "Service type exposure defaults",
            "url": "https://kubernetes.io/docs/concepts/services-networking/service/",
        }
    ],
    "oci": [
        {
            "topic": "OCI public subnet and internet gateway behavior",
            "url": "https://docs.oracle.com/iaas/Content/Network/Tasks/managingIGs.htm",
        }
    ],
    "alicloud": [
        {
            "topic": "Alibaba Cloud security group and internet exposure",
            "url": "https://www.alibabacloud.com/help/en/ecs/user-guide/security-groups",
        }
    ],
}

SCAN_SOURCE_SUFFIXES = {
    ".tf",
    ".tfvars",
    ".hcl",
    ".bicep",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
}

EXPECTED_ASSET_PATTERNS: dict[str, tuple[str, ...]] = {
    "internet_ingress": (
        r"\b(public\s+ip|internet|internet-facing|ingress|load\s*balancer|application\s+gateway|api\s+gateway|apim|front\s+door|cloudfront)\b",
    ),
    "database": (
        r"\b(database|sql|postgres|mysql|mssql|rds|cosmos)\b",
    ),
    "storage": (
        r"\b(storage\s+account|blob|bucket|s3|object\s+storage)\b",
    ),
    "queue": (
        r"\b(queue|service\s+bus|sqs|pubsub|topic|event\s*hub|kafka)\b",
    ),
}

ASSET_TO_DIAGRAM_TERMS: dict[str, tuple[str, ...]] = {
    "internet_ingress": (
        "internet",
        "public",
        "ingress",
        "gateway",
        "load balancer",
        "application gateway",
        "api gateway",
        "apim",
        "front door",
        "cloudfront",
    ),
    "database": ("database", "db", "sql", "rds", "cosmos", "postgres", "mysql"),
    "storage": ("storage", "bucket", "blob", "s3", "object"),
    "queue": ("queue", "service bus", "sqs", "pubsub", "topic", "event hub", "kafka"),
}


@dataclass
class RepoOption:
    name: str
    path: str

    @property
    def api_key(self) -> str:
        """Canonical key used by web API scan-status routes."""
        return Path(self.path).name or self.name


@dataclass
class ScanResult:
    repo_name: str
    repo_path: str
    experiment_id: str | None
    status: str
    error: str = ""
    provider_screenshots: list[dict[str, Any]] | None = None
    orphan_issues: list[dict[str, Any]] | None = None
    connection_issues: list[dict[str, Any]] | None = None
    parity_issues: list[dict[str, Any]] | None = None
    rule_candidates: list[dict[str, Any]] | None = None


def _result_repo_key(repo_name: str, repo_path: str) -> tuple[str, str]:
    return (_normalize_path(repo_path).lower(), repo_name.lower())


def scan_result_to_dict(result: ScanResult) -> dict[str, Any]:
    return {
        "repo_name": result.repo_name,
        "repo_path": result.repo_path,
        "experiment_id": result.experiment_id,
        "status": result.status,
        "error": result.error,
        "provider_screenshots": result.provider_screenshots or [],
        "orphan_issues": result.orphan_issues or [],
        "connection_issues": result.connection_issues or [],
        "parity_issues": result.parity_issues or [],
        "rule_candidates": result.rule_candidates or [],
    }


def build_retry_metadata(
    primary_results: list[ScanResult],
    retry_results: list[ScanResult],
) -> list[dict[str, Any]]:
    retry_by_repo = {
        _result_repo_key(result.repo_name, result.repo_path): result
        for result in retry_results
    }
    retried: list[dict[str, Any]] = []
    for primary in primary_results:
        key = _result_repo_key(primary.repo_name, primary.repo_path)
        retry = retry_by_repo.get(key)
        if not retry:
            continue
        retried.append(
            {
                "repo_name": primary.repo_name,
                "repo_path": primary.repo_path,
                "primary_status": primary.status,
                "primary_error": primary.error,
                "retry_status": retry.status,
                "retry_error": retry.error,
                "retry_passed": retry.status == "completed",
            }
        )
    return sorted(retried, key=lambda x: x["repo_name"].lower())


def merge_final_results(primary_results: list[ScanResult], retry_results: list[ScanResult]) -> list[ScanResult]:
    merged = {
        _result_repo_key(result.repo_name, result.repo_path): result
        for result in primary_results
    }
    for retry in retry_results:
        merged[_result_repo_key(retry.repo_name, retry.repo_path)] = retry
    return sorted(merged.values(), key=lambda x: x.repo_name.lower())


def make_failed_result(repo: RepoOption, error: str) -> ScanResult:
    return ScanResult(
        repo_name=repo.name,
        repo_path=repo.path,
        experiment_id=None,
        status="failed",
        error=error,
        provider_screenshots=[],
        orphan_issues=[],
        connection_issues=[],
        parity_issues=[],
        rule_candidates=[],
    )


def load_repo_search_roots(settings_file: Path = SETTINGS_PATH) -> list[Path]:
    """Load repo search roots from Settings/paths.json using web/app.py semantics."""
    if settings_file.exists():
        try:
            config = json.loads(settings_file.read_text(encoding="utf-8"))
            configured = config.get("repo_search_paths", [])
            if isinstance(configured, list) and configured:
                return [Path(str(p)).expanduser() for p in configured]
        except Exception:
            pass
    return [
        REPO_ROOT.parent,
        Path.home() / "repos",
        Path.home() / "code",
        Path.home() / "projects",
        Path.home(),
    ]


def resolve_intake_repos(
    intake_file: Path = INTAKE_REPOS_FILE,
    search_roots: list[Path] | None = None,
) -> tuple[list[RepoOption], list[dict[str, Any]], list[str]]:
    """Resolve Intake/ReposToScan.txt entries to on-disk repo options."""
    roots = search_roots or load_repo_search_roots()
    found: list[RepoOption] = []
    unresolved: list[dict[str, Any]] = []
    root_strings = [str(r) for r in roots]
    if not intake_file.exists():
        return found, unresolved, root_strings

    for line in intake_file.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        resolved: Path | None = None
        for root in roots:
            candidate = root / name
            if candidate.is_dir():
                resolved = candidate.resolve()
                break
        if resolved:
            found.append(RepoOption(name=name, path=str(resolved)))
        else:
            unresolved.append({"name": name, "search_roots": root_strings})
    return found, unresolved, root_strings


def _normalize_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return str(Path(value).expanduser())


def filter_repos_to_resolved_intake(
    dropdown_repos: list[RepoOption],
    intake_resolved_repos: list[RepoOption],
) -> tuple[list[RepoOption], list[RepoOption]]:
    """Keep only dropdown repos that map to a resolved Intake repo path."""
    allowed_paths = {_normalize_path(r.path) for r in intake_resolved_repos if r.path}
    targeted: list[RepoOption] = []
    skipped: list[RepoOption] = []
    for repo in dropdown_repos:
        if _normalize_path(repo.path) in allowed_paths:
            targeted.append(repo)
        else:
            skipped.append(repo)
    return targeted, skipped


def validate_partition_args(
    partition_count: int | None,
    partition_index: int | None,
) -> tuple[int | None, int | None]:
    if partition_count is None and partition_index is None:
        return None, None
    if partition_count is None or partition_index is None:
        raise ValueError("--partition-count and --partition-index must be provided together")
    if partition_count < 1:
        raise ValueError("--partition-count must be >= 1")
    if partition_index < 0:
        raise ValueError("--partition-index must be >= 0")
    if partition_index >= partition_count:
        raise ValueError("--partition-index must be less than --partition-count")
    return partition_count, partition_index


def partition_repos(
    repos: list[RepoOption],
    partition_count: int | None,
    partition_index: int | None,
) -> list[RepoOption]:
    if partition_count is None or partition_index is None:
        return repos
    ordered = sorted(repos, key=lambda r: (_normalize_path(r.path).lower(), r.name.lower()))
    return [repo for idx, repo in enumerate(ordered) if idx % partition_count == partition_index]


def effective_concurrency(mode: str, requested_concurrency: int) -> int:
    if mode == "serial":
        return 1
    return requested_concurrency


class ProgressLogger:
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def info(self, msg: str) -> None:
        await self._write("INFO", msg)

    async def warn(self, msg: str) -> None:
        await self._write("WARN", msg)

    async def error(self, msg: str) -> None:
        await self._write("ERROR", msg)

    async def improvement(self, msg: str) -> None:
        await self._write("IMPROVEMENT", msg)

    async def _write(self, level: str, msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat()}] [{level}] {msg}"
        async with self._lock:
            print(line, flush=True)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def _parse_provider_from_title(title: str) -> str:
    t = (title or "").lower()
    for key in ("azure", "aws", "gcp", "kubernetes", "oci", "alicloud"):
        if key in t:
            return key
    return "unknown"


def _extract_node_ids(code: str) -> set[str]:
    node_ids: set[str] = set()
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith("%%"):
            continue
        lower = s.lower()
        if lower.startswith(("flowchart", "subgraph", "classdef", "class ", "style ", "linkstyle", "end")):
            continue
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9_]*)\s*[\[\(\{]", s):
            node_ids.add(m.group(1))
    return node_ids


def _extract_edges(code: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    edge_re = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*(?:-->|-\.->|==>)\s*([A-Za-z][A-Za-z0-9_]*)")
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith("%%"):
            continue
        s = re.sub(r"([A-Za-z][A-Za-z0-9_]*)\s*(?:\[[^\]]*\]|\([^\)]*\)|\{[^\}]*\})", r"\1", s)
        s = re.sub(r"\|[^|]*\|", "", s)
        s = re.sub(r"--[^-<>]*-->", "-->", s)
        for m in edge_re.finditer(s):
            edges.append((m.group(1), m.group(2)))
    return edges


def _extract_node_label_map(code: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    pattern = re.compile(
        r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\[\[|\[\(|\[|\(\[|\(\"|\(\(|\{)([^\]\)\}\n]{1,200})",
    )
    for m in pattern.finditer(code):
        node_id = m.group(1)
        label = m.group(2).strip().strip('"').strip("'").strip()
        if label:
            labels[node_id] = label
    return labels


def _extract_diagram_text(code: str) -> str:
    labels = _extract_node_label_map(code)
    return " ".join(labels.values()).lower()


def _path_exists(edges: list[tuple[str, str]], sources: set[str], targets: set[str]) -> bool:
    if not sources or not targets:
        return False
    graph: dict[str, set[str]] = {}
    for src, dst in edges:
        graph.setdefault(src, set()).add(dst)
    frontier = list(sources)
    seen = set(frontier)
    while frontier:
        cur = frontier.pop()
        if cur in targets:
            return True
        for nxt in graph.get(cur, set()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


def detect_missing_connections(code: str, provider: str, diagram_title: str) -> list[dict[str, Any]]:
    labels = _extract_node_label_map(code)
    edges = _extract_edges(code)
    id_to_text = {node_id: (label or "").lower() for node_id, label in labels.items()}

    internet_nodes = {
        node_id
        for node_id, text in id_to_text.items()
        if any(term in text for term in ("internet", "public internet"))
    }
    ingress_nodes = {
        node_id
        for node_id, text in id_to_text.items()
        if any(term in text for term in ("ingress", "gateway", "load balancer", "front door", "apim"))
    }
    service_nodes = {
        node_id
        for node_id, text in id_to_text.items()
        if any(term in text for term in ("service", "backend", "api", "function", "lambda", "container", "app"))
    }
    data_nodes = {
        node_id
        for node_id, text in id_to_text.items()
        if any(term in text for term in ("database", "db", "sql", "storage", "bucket", "blob", "queue", "topic", "cache"))
    }

    issues: list[dict[str, Any]] = []
    if internet_nodes and ingress_nodes and not _path_exists(edges, internet_nodes, ingress_nodes):
        issues.append(
            {
                "issue_type": "missing_internet_to_ingress",
                "diagram_title": diagram_title,
                "provider": provider,
                "description": "Internet/public node exists but no path reaches ingress/gateway nodes.",
                "repo_evidence_files": [],
                "provider_default_refs": PROVIDER_DEFAULT_REFERENCES.get(provider, []),
            }
        )
    if ingress_nodes and service_nodes and not _path_exists(edges, ingress_nodes, service_nodes):
        issues.append(
            {
                "issue_type": "missing_ingress_to_service",
                "diagram_title": diagram_title,
                "provider": provider,
                "description": "Ingress/gateway nodes exist but no path reaches application service nodes.",
                "repo_evidence_files": [],
                "provider_default_refs": PROVIDER_DEFAULT_REFERENCES.get(provider, []),
            }
        )
    if service_nodes and data_nodes and not _path_exists(edges, service_nodes, data_nodes):
        issues.append(
            {
                "issue_type": "missing_service_to_data",
                "diagram_title": diagram_title,
                "provider": provider,
                "description": "Service/application nodes exist but no path reaches data-tier nodes.",
                "repo_evidence_files": [],
                "provider_default_refs": PROVIDER_DEFAULT_REFERENCES.get(provider, []),
            }
        )
    return issues


def find_orphan_nodes(code: str) -> list[str]:
    nodes = _extract_node_ids(code)
    edges = _extract_edges(code)
    if not nodes:
        return []
    degree: dict[str, int] = {n: 0 for n in nodes}
    for src, dst in edges:
        if src in degree:
            degree[src] += 1
        if dst in degree:
            degree[dst] += 1
    orphans = [
        node
        for node, d in degree.items()
        if d == 0 and node.lower() not in IGNORED_ORPHAN_NODE_IDS
    ]
    return sorted(orphans)


def infer_expected_assets(repo_path: Path) -> dict[str, list[str]]:
    expected: dict[str, list[str]] = {}
    if not repo_path.is_dir():
        return expected
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in (".git", ".venv", "node_modules", ".terraform", "Output")):
            continue
        suffix = path.suffix.lower()
        if suffix not in SCAN_SOURCE_SUFFIXES and path.name.lower() not in {"readme", "readme.md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:200000].lower()
        except Exception:
            continue
        rel_path = str(path.relative_to(repo_path))
        for asset, patterns in EXPECTED_ASSET_PATTERNS.items():
            if any(re.search(pattern, text) for pattern in patterns):
                expected.setdefault(asset, [])
                if rel_path not in expected[asset]:
                    expected[asset].append(rel_path)
    return expected


def detect_docs_iac_parity_issues(
    code: str,
    provider: str,
    diagram_title: str,
    expected_assets: dict[str, list[str]],
) -> list[dict[str, Any]]:
    diagram_text = _extract_diagram_text(code)
    issues: list[dict[str, Any]] = []
    for asset, evidence_files in expected_assets.items():
        required_terms = ASSET_TO_DIAGRAM_TERMS.get(asset, ())
        if required_terms and any(term in diagram_text for term in required_terms):
            continue
        issues.append(
            {
                "issue_type": "docs_iac_parity_gap",
                "expected_asset": asset,
                "diagram_title": diagram_title,
                "provider": provider,
                "description": (
                    f"Repo docs/IaC suggest '{asset}' but diagram nodes/labels do not clearly represent it."
                ),
                "repo_evidence_files": evidence_files[:5],
                "provider_default_refs": PROVIDER_DEFAULT_REFERENCES.get(provider, []),
            }
        )
    return issues


def gather_repo_evidence(repo_path: Path, node_id: str, max_hits: int = 3) -> list[str]:
    terms = [t for t in re.split(r"[_\-.]+", node_id.lower()) if len(t) >= 3]
    if not terms:
        terms = [node_id.lower()]
    hits: list[str] = []
    for path in repo_path.rglob("*"):
        if len(hits) >= max_hits:
            break
        if not path.is_file():
            continue
        if path.suffix.lower() not in EVIDENCE_SUFFIXES and path.name.lower() not in {"readme", "readme.md"}:
            continue
        if any(skip in path.parts for skip in (".git", ".venv", "node_modules", "Output")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        text_l = text.lower()
        if all(term in text_l for term in terms[:2]):
            hits.append(str(path.relative_to(repo_path)))
    return hits


def write_rule_candidate_stubs(
    repo: RepoOption,
    issues: list[dict[str, Any]],
    candidates_dir: Path,
) -> list[dict[str, Any]]:
    """Write detection-rule candidates for unresolved diagram/coverage issues."""
    candidates_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for issue in issues:
        if not issue.get("repo_evidence_files"):
            continue
        provider = issue.get("provider") or "unknown"
        issue_type = issue.get("issue_type") or "coverage_gap"
        issue_key = issue.get("node_id") or issue.get("expected_asset") or issue_type
        clean_node = _slug(str(issue_key).lower())
        rule_id = f"context-{provider}-{clean_node}-detection"
        path_regex = "(?:\\.tf|\\.yaml|\\.yml|README\\.md)$"
        pattern_regex = rf"(?i){re.escape(str(issue_key).replace('_', ' '))}"
        content = "\n".join(
            [
                "rules:",
                f"  - id: {rule_id}",
                "    message: |",
                f"      Candidate detection rule generated for {issue_type} ('{issue_key}') in repo '{repo.name}'.",
                "      Confirm exact IaC/resource shape and tighten this rule before production use.",
                "    severity: INFO",
                "    languages: [regex]",
                "    patterns:",
                f"      - pattern-regex: '{pattern_regex}'",
                "    paths:",
                "      include:",
                f"        - '*{path_regex}'",
                "    metadata:",
                "      category: security",
                "      subcategory: [asset-discovery-gap]",
                f"      technology: [{provider}]",
                "      finding_kind: asset_detection",
                "      generated_by: web_parallel_scan_validator",
            ]
        ) + "\n"
        rule_file = candidates_dir / f"{rule_id}.yml"
        rule_file.write_text(content, encoding="utf-8")
        written.append(
            {
                "rule_id": rule_id,
                "rule_file": str(rule_file),
                "validate_command": f"opengrep scan --config {rule_file} {repo.path}",
                "mapping_hint": f"Add '{rule_id}' to Scripts/Scan/targeted_scan.py DETECTION_TO_MISCONFIG",
            }
        )
    return written


async def discover_repos(page) -> list[RepoOption]:
    await page.goto("/", wait_until="domcontentloaded")
    await page.wait_for_selector("#repo-select")
    rows = await page.evaluate(
        """
        () => {
          const select = document.querySelector('#repo-select');
          const out = [];
          for (const opt of select.options) {
            if (!opt.value || opt.disabled) continue;
            out.push({
              name: (opt.dataset?.name || opt.textContent || '').split('—')[0].trim(),
              path: opt.value.trim(),
            });
          }
          return out;
        }
        """
    )
    return [RepoOption(name=r["name"], path=r["path"]) for r in rows if r.get("path")]


async def start_scan_from_ui(page, repo: RepoOption, logger: ProgressLogger) -> None:
    await page.goto("/", wait_until="domcontentloaded")
    await page.wait_for_selector("#repo-select")
    await page.select_option("#repo-select", value=repo.path)
    await page.click("#scan-btn")
    await asyncio.sleep(1.0)
    modal_visible = await page.locator("#scan-modal").is_visible()
    if modal_visible:
        await logger.warn(f"{repo.name}: existing running scan modal detected, switching to watch mode")
        await page.click("#modal-watch")


async def wait_for_experiment(page, repo_name: str, logger: ProgressLogger) -> str:
    escaped = quote(repo_name, safe="")
    deadline = asyncio.get_running_loop().time() + SCAN_START_TIMEOUT_SEC
    found: str | None = None
    while asyncio.get_running_loop().time() < deadline:
        response = await page.request.get(f"/api/scans/{escaped}")
        payload = await response.json()
        running = payload.get("running_experiment")
        if running:
            found = str(running)
            break
        await asyncio.sleep(2)
    if not found:
        raise TimeoutError(f"{repo_name}: timed out waiting for running experiment id")
    await logger.info(f"{repo_name}: scan attached to experiment {found}")
    return found


async def wait_for_completion(
    page,
    repo_status_key: str,
    experiment_id: str,
    logger: ProgressLogger,
    timeout_sec: int,
    repo_display_name: str | None = None,
) -> None:
    escaped = quote(repo_status_key, safe="")
    display_name = repo_display_name or repo_status_key
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        response = await page.request.get(f"/api/scans/{escaped}")
        payload = await response.json()
        done, reason = should_finish_wait(
            running_experiment=payload.get("running_experiment"),
            scans=payload.get("scans") or [],
            expected_experiment_id=experiment_id,
        )
        if done:
            if reason == "completed":
                await logger.info(f"{display_name}: experiment {experiment_id} completed")
            else:
                await logger.warn(
                    f"{display_name}: scan stopped running but experiment {experiment_id} "
                    "is missing from scan history; treating as terminal failure path"
                )
            return
        await asyncio.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(
        f"{display_name}: scan did not complete within configured timeout ({timeout_sec}s)"
    )


def should_finish_wait(
    running_experiment: Any,
    scans: list[dict[str, Any]],
    expected_experiment_id: str,
) -> tuple[bool, str]:
    """Decide whether scan wait should terminate.

    Returns (done, reason), where reason is one of:
    - running: experiment still running
    - completed: experiment found in scan history
    - no_history_record: no running scan and no DB scan record
    """
    if running_experiment is not None:
        return (False, "running")
    if any(str(s.get("experiment_id")) == str(expected_experiment_id) for s in (scans or [])):
        return (True, "completed")
    return (True, "no_history_record")


async def capture_provider_screenshots(browser, base_url: str, repo_name: str, experiment_id: str, output_dir: Path, logger: ProgressLogger) -> list[dict[str, Any]]:
    screenshots: list[dict[str, Any]] = []
    context = await browser.new_context(base_url=base_url)
    page = await context.new_page()
    try:
        await page.goto(f"/diagrams/{experiment_id}", wait_until="domcontentloaded")
        await page.wait_for_selector("#diagram-container", timeout=15000)
        tabs = page.locator("[role='tab'][data-provider]")
        tab_count = await tabs.count()

        if tab_count == 0:
            provider = (await page.locator("#header-provider").inner_text()).strip().lower() or "unknown"
            await page.wait_for_timeout(800)
            has_svg = await page.locator("#diagram-container svg").count() > 0
            shot = output_dir / f"{_slug(repo_name)}_{experiment_id}_{_slug(provider)}.png"
            await page.screenshot(path=str(shot), full_page=True)
            screenshots.append({"provider": provider, "screenshot": str(shot), "rendered_svg": bool(has_svg)})
            await logger.info(f"{repo_name}: captured screenshot for provider={provider} rendered_svg={has_svg}")
        else:
            for idx in range(tab_count):
                tab = tabs.nth(idx)
                provider = (await tab.get_attribute("data-provider")) or f"provider_{idx}"
                await tab.click()
                await page.wait_for_timeout(1200)
                has_svg = await page.locator("#diagram-container svg").count() > 0
                shot = output_dir / f"{_slug(repo_name)}_{experiment_id}_{_slug(provider)}.png"
                await page.screenshot(path=str(shot), full_page=True)
                screenshots.append({"provider": provider, "screenshot": str(shot), "rendered_svg": bool(has_svg)})
                await logger.info(f"{repo_name}: captured screenshot for provider={provider} rendered_svg={has_svg}")
    finally:
        await context.close()
    return screenshots


async def collect_diagram_validation_issues(
    page,
    repo: RepoOption,
    experiment_id: str,
    logger: ProgressLogger,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    escaped_repo = quote(repo.api_key, safe="")
    response = await page.request.get(f"/api/diagrams/{experiment_id}?repo_name={escaped_repo}")
    if not response.ok:
        raise RuntimeError(f"{repo.name}: failed to load diagrams payload ({response.status})")
    payload = await response.json()
    diagrams = payload.get("diagrams") or []
    orphan_issues: list[dict[str, Any]] = []
    connection_issues: list[dict[str, Any]] = []
    parity_issues: list[dict[str, Any]] = []
    repo_root = Path(repo.path)
    expected_assets = infer_expected_assets(repo_root) if repo_root.is_dir() else {}
    for d in diagrams:
        title = d.get("title") or "Untitled diagram"
        provider = _parse_provider_from_title(title)
        code = d.get("code") or ""
        orphans = find_orphan_nodes(code)
        for orphan in orphans:
            evidence = gather_repo_evidence(repo_root, orphan) if repo_root.is_dir() else []
            refs = PROVIDER_DEFAULT_REFERENCES.get(provider, [])
            issue = {
                "issue_type": "orphan_node",
                "diagram_title": title,
                "provider": provider,
                "node_id": orphan,
                "repo_evidence_files": evidence,
                "provider_default_refs": refs,
            }
            orphan_issues.append(issue)
            await logger.warn(
                f"{repo.name}: orphan node '{orphan}' in '{title}' "
                f"(evidence_files={len(evidence)}, provider_refs={len(refs)})"
            )
        missing_connections = detect_missing_connections(code, provider, title)
        connection_issues.extend(missing_connections)
        for issue in missing_connections:
            await logger.warn(f"{repo.name}: {issue['issue_type']} in '{title}'")

        parity = detect_docs_iac_parity_issues(
            code=code,
            provider=provider,
            diagram_title=title,
            expected_assets=expected_assets,
        )
        parity_issues.extend(parity)
        for issue in parity:
            await logger.warn(
                f"{repo.name}: docs+IaC parity gap '{issue.get('expected_asset', 'unknown')}' in '{title}' "
                f"(evidence_files={len(issue.get('repo_evidence_files') or [])})"
            )
    return orphan_issues, connection_issues, parity_issues


async def scan_repo_worker(
    browser,
    base_url: str,
    repo: RepoOption,
    screenshots_dir: Path,
    candidate_rules_dir: Path,
    write_rule_candidates: bool,
    scan_complete_timeout_sec: int,
    logger: ProgressLogger,
) -> ScanResult:
    context = await browser.new_context(base_url=base_url)
    page = await context.new_page()
    try:
        await logger.info(f"{repo.name}: starting scan for {repo.path}")
        await start_scan_from_ui(page, repo, logger)
        experiment_id = await wait_for_experiment(page, repo.api_key, logger)
        await wait_for_completion(
            page=page,
            repo_status_key=repo.api_key,
            experiment_id=experiment_id,
            logger=logger,
            timeout_sec=scan_complete_timeout_sec,
            repo_display_name=repo.name,
        )

        provider_screenshots = await capture_provider_screenshots(
            browser=browser,
            base_url=base_url,
            repo_name=repo.name,
            experiment_id=experiment_id,
            output_dir=screenshots_dir,
            logger=logger,
        )
        orphan_issues, connection_issues, parity_issues = await collect_diagram_validation_issues(
            page,
            repo,
            experiment_id,
            logger,
        )
        rule_candidates: list[dict[str, Any]] = []
        candidate_input = [*orphan_issues, *connection_issues, *parity_issues]
        if write_rule_candidates and candidate_input:
            rule_candidates = write_rule_candidate_stubs(repo, candidate_input, candidate_rules_dir)
            if rule_candidates:
                await logger.improvement(
                    f"{repo.name}: wrote {len(rule_candidates)} candidate detection rules"
                )

        if candidate_input:
            await logger.improvement(
                f"{repo.name}: generated {len(candidate_input)} diagram/data-backed improvement candidates"
            )

        return ScanResult(
            repo_name=repo.name,
            repo_path=repo.path,
            experiment_id=experiment_id,
            status="completed",
            provider_screenshots=provider_screenshots,
            orphan_issues=orphan_issues,
            connection_issues=connection_issues,
            parity_issues=parity_issues,
            rule_candidates=rule_candidates,
        )
    except TimeoutError as exc:
        await logger.warn(
            f"{repo.name}: timeout safeguard triggered ({exc}); moving on to next repo in batch"
        )
        return ScanResult(
            repo_name=repo.name,
            repo_path=repo.path,
            experiment_id=None,
            status="failed",
            error=str(exc),
            provider_screenshots=[],
            orphan_issues=[],
            connection_issues=[],
            parity_issues=[],
            rule_candidates=[],
        )
    except Exception as exc:  # explicit surface, no silent failure
        await logger.error(f"{repo.name}: failed with error: {exc}")
        return ScanResult(
            repo_name=repo.name,
            repo_path=repo.path,
            experiment_id=None,
            status="failed",
            error=str(exc),
            provider_screenshots=[],
            orphan_issues=[],
            connection_issues=[],
            parity_issues=[],
            rule_candidates=[],
        )
    finally:
        await context.close()


async def run(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        print(
            "Playwright is required for this validator. Install with:\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            flush=True,
        )
        raise SystemExit(2) from exc

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.audit_root).resolve() / f"WebScanValidation_{run_stamp}"
    screenshots_dir = run_dir / "screenshots"
    candidates_dir = run_dir / "rule_candidates"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    logger = ProgressLogger(run_dir / "improvements.log")
    await logger.info("Starting headless web scan validator")
    await logger.info(f"Base URL: {args.base_url}")
    worker_concurrency = effective_concurrency(args.mode, args.concurrency)
    await logger.info(f"Mode: {args.mode}")
    await logger.info(f"Concurrency: {worker_concurrency} (requested={args.concurrency})")
    await logger.info(f"Per-repo completion timeout: {args.scan_complete_timeout_sec}s")

    intake_resolved, intake_unresolved, search_roots = resolve_intake_repos()
    if intake_unresolved:
        unresolved_names = [r["name"] for r in intake_unresolved]
        await logger.warn(
            f"Skipping {len(intake_unresolved)} unresolved Intake repo(s): {unresolved_names}; "
            f"search paths={search_roots}"
        )
    else:
        await logger.info("All Intake/ReposToScan.txt entries resolved on disk")

    dropdown_repos: list[RepoOption] = []
    skipped_dropdown_repos: list[RepoOption] = []
    repos: list[RepoOption] = []
    primary_results: list[ScanResult] = []
    retry_results: list[ScanResult] = []
    final_results: list[ScanResult] = []
    run_error: str | None = None
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            context = await browser.new_context(base_url=args.base_url)
            page = await context.new_page()
            dropdown_repos = await discover_repos(page)
            repos, skipped_dropdown_repos = filter_repos_to_resolved_intake(dropdown_repos, intake_resolved)
            await context.close()

            if skipped_dropdown_repos:
                await logger.warn(
                    f"Skipping {len(skipped_dropdown_repos)} dropdown repo(s) not resolvable via Intake/search paths: "
                    f"{[r.name for r in skipped_dropdown_repos]}"
                )

            if args.repos:
                filter_set = {r.strip().lower() for r in args.repos if r.strip()}
                repos = [r for r in repos if r.name.lower() in filter_set]
                await logger.info(f"Repo filter applied: {sorted(filter_set)}")

            repos = partition_repos(repos, args.partition_count, args.partition_index)
            if args.partition_count is not None:
                await logger.info(
                    f"Partition filter applied: index={args.partition_index}/{args.partition_count - 1}, "
                    f"targeting {len(repos)} repo(s)"
                )

            if not repos:
                await logger.error("No repositories available from UI dropdown after filtering")
                run_error = "No repositories available from UI dropdown after filtering"
            else:
                await logger.info(
                    f"Discovered {len(dropdown_repos)} repositories from dropdown, targeting {len(repos)} resolved repo(s)"
                )
                semaphore = asyncio.Semaphore(worker_concurrency)
                worker_timeout_sec = args.scan_complete_timeout_sec + WORKER_TIMEOUT_GRACE_SEC

                async def execute_pass(pass_name: str, target_repos: list[RepoOption]) -> list[ScanResult]:
                    await logger.info(f"Starting {pass_name} pass for {len(target_repos)} repo(s)")
                    pass_results: list[ScanResult] = []

                    async def _run_one(repo: RepoOption):
                        async with semaphore:
                            try:
                                result = await asyncio.wait_for(
                                    scan_repo_worker(
                                        browser=browser,
                                        base_url=args.base_url,
                                        repo=repo,
                                        screenshots_dir=screenshots_dir,
                                        candidate_rules_dir=candidates_dir,
                                        write_rule_candidates=bool(args.write_rule_candidates),
                                        scan_complete_timeout_sec=args.scan_complete_timeout_sec,
                                        logger=logger,
                                    ),
                                    timeout=worker_timeout_sec,
                                )
                            except TimeoutError:
                                msg = (
                                    f"{repo.name}: worker lifecycle timeout after {worker_timeout_sec}s; "
                                    "marking failed and continuing"
                                )
                                await logger.warn(msg)
                                result = make_failed_result(repo, msg)
                            except Exception as exc:
                                await logger.error(f"{repo.name}: worker crashed unexpectedly: {exc}")
                                result = make_failed_result(repo, str(exc))
                            pass_results.append(result)
                            if result.status == "completed":
                                await logger.info(f"{repo.name}: {pass_name} pass ended with status=completed")
                            else:
                                await logger.warn(
                                    f"{repo.name}: {pass_name} pass ended with status=failed; "
                                    "batch will continue without stalling"
                                )

                    if args.mode == "serial":
                        for repo in target_repos:
                            await _run_one(repo)
                    else:
                        tasks = [asyncio.create_task(_run_one(repo)) for repo in target_repos]
                        await asyncio.gather(*tasks, return_exceptions=True)
                        pending_tasks = [task for task in tasks if not task.done()]
                        for task in pending_tasks:
                            task.cancel()
                        if pending_tasks:
                            await asyncio.gather(*pending_tasks, return_exceptions=True)
                    await logger.info(
                        f"Finished {pass_name} pass: completed="
                        f"{sum(1 for r in pass_results if r.status == 'completed')} "
                        f"failed={sum(1 for r in pass_results if r.status != 'completed')}"
                    )
                    return pass_results

                primary_results = await execute_pass("primary", repos)
                primary_failed_keys = {
                    _result_repo_key(result.repo_name, result.repo_path)
                    for result in primary_results
                    if result.status != "completed"
                }
                retry_repos = [
                    repo for repo in repos if _result_repo_key(repo.name, repo.path) in primary_failed_keys
                ]
                if retry_repos:
                    await logger.warn(
                        f"Primary pass complete; retrying failed repos once at end: "
                        f"{[repo.name for repo in retry_repos]}"
                    )
                    retry_results = await execute_pass("retry", retry_repos)
                    await logger.info("Retry pass complete")
                else:
                    await logger.info("Primary pass complete with no failed repos; retry pass skipped")

                final_results = merge_final_results(primary_results, retry_results)
    except Exception as exc:
        run_error = str(exc)
        await logger.error(f"Run-level failure while scanning repos: {exc}")
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                await logger.warn(f"Error while closing browser during finalization: {exc}")

    if not final_results:
        final_results = merge_final_results(primary_results, retry_results)
    known_keys = {_result_repo_key(result.repo_name, result.repo_path) for result in final_results}
    for repo in repos:
        key = _result_repo_key(repo.name, repo.path)
        if key not in known_keys:
            final_results.append(
                make_failed_result(
                    repo,
                    run_error or f"{repo.name}: scan did not produce a terminal result before finalization",
                )
            )
    final_results = sorted(
        final_results,
        key=lambda x: (_normalize_path(x.repo_path).lower(), x.repo_name.lower()),
    )

    retry_metadata = build_retry_metadata(primary_results, retry_results)
    summary = {
        "run_timestamp": run_stamp,
        "base_url": args.base_url,
        "mode": args.mode,
        "concurrency": args.concurrency,
        "effective_concurrency": worker_concurrency,
        "scan_complete_timeout_sec": args.scan_complete_timeout_sec,
        "partition_count": args.partition_count,
        "partition_index": args.partition_index,
        "repos_discovered_from_dropdown": len(dropdown_repos),
        "repos_resolved_from_intake": len(intake_resolved),
        "repos_unresolved_from_intake": intake_unresolved,
        "repos_skipped_from_dropdown": [
            {"name": r.name, "path": r.path} for r in sorted(skipped_dropdown_repos, key=lambda x: x.name.lower())
        ],
        "repos_total": len(repos),
        "primary_completed": sum(1 for r in primary_results if r.status == "completed"),
        "primary_failed": sum(1 for r in primary_results if r.status != "completed"),
        "retry_attempted": len(retry_results),
        "retry_recovered": sum(1 for r in retry_results if r.status == "completed"),
        "retried_repos": retry_metadata,
        "completed": sum(1 for r in final_results if r.status == "completed"),
        "failed": sum(1 for r in final_results if r.status != "completed"),
        "results": [scan_result_to_dict(r) for r in final_results],
    }
    summary_file = run_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    await logger.info(
        f"Run complete. Final status completed={summary['completed']} failed={summary['failed']}. "
        f"Summary written to {summary_file}"
    )
    await logger.info(f"Screenshots folder: {screenshots_dir}")
    if args.write_rule_candidates:
        await logger.info(f"Rule candidate folder: {candidates_dir}")
    await logger.improvement(
        "Improvement log includes orphan-node findings with repo evidence hints and provider-default reference links"
    )
    return 0 if summary["failed"] == 0 and run_error is None else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless web scan + diagram validator (parallel or serial execution)."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Web UI base URL (default: http://127.0.0.1:9000)")
    parser.add_argument("--concurrency", type=int, default=6, help="Maximum concurrent scans (default: 6)")
    parser.add_argument(
        "--mode",
        choices=("parallel", "serial"),
        default="parallel",
        help="Execution mode: parallel (default) or serial one-by-one.",
    )
    parser.add_argument("--audit-root", default=str(DEFAULT_AUDIT_ROOT), help="Audit output root directory")
    parser.add_argument("--repos", nargs="*", help="Optional repo names to include (defaults to all dropdown repos)")
    parser.add_argument("--partition-count", type=int, help="Total partition count for deterministic repo sharding")
    parser.add_argument("--partition-index", type=int, help="Zero-based partition index to execute")
    parser.add_argument(
        "--scan-complete-timeout-sec",
        type=int,
        default=DEFAULT_SCAN_COMPLETE_TIMEOUT_SEC,
        help=f"Per-repo scan completion timeout in seconds (default: {DEFAULT_SCAN_COMPLETE_TIMEOUT_SEC})",
    )
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument(
        "--write-rule-candidates",
        action="store_true",
        help="Write candidate detection rules for orphan nodes with repo evidence",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    if args.concurrency < 1:
        print("--concurrency must be >= 1", flush=True)
        return 2
    if args.scan_complete_timeout_sec < 1:
        print("--scan-complete-timeout-sec must be >= 1", flush=True)
        return 2
    try:
        args.partition_count, args.partition_index = validate_partition_args(
            args.partition_count,
            args.partition_index,
        )
    except ValueError as exc:
        print(str(exc), flush=True)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
