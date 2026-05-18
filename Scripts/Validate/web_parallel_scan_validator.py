#!/usr/bin/env python3
"""Run headless web scans in parallel and validate rendered cloud diagrams.

Workflow:
1. Open the web UI and read all repositories from the dropdown.
2. Start scans with bounded concurrency (default: 6).
3. Process results as each repo completes (timeouts fail fast and move on).
4. Retry failed repositories once after the primary pass.
   - Optional strict mode: retry immediately per-repo before continuing.
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
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]

# Import rendering validation module
try:
    import sys
    sys.path.insert(0, str(REPO_ROOT / "Scripts" / "Validate"))
    from rendering_validation import (
        validate_icon_availability,
        validate_icon_mapping_semantics,
        validate_rendering_pipeline,
        generate_asset_validation_report,
    )
except ImportError as e:
    print(f"Warning: Could not import rendering_validation: {e}")
    validate_icon_availability = None
    validate_icon_mapping_semantics = None
    validate_rendering_pipeline = None
    generate_asset_validation_report = None
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
        r"\b(azurerm_servicebus_(namespace|queue|topic|subscription)|aws_sqs_queue|aws_sns_topic|google_pubsub_(topic|subscription)|servicebus|eventhub|kafka)\b",
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
    hierarchy_issues: list[dict[str, Any]] | None = None
    resource_value_assessments: list[dict[str, Any]] | None = None
    rule_candidates: list[dict[str, Any]] | None = None
    detection_rules: list[dict[str, Any]] | None = None
    rule_validations: list[dict[str, Any]] | None = None


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
        "hierarchy_issues": result.hierarchy_issues or [],
        "resource_value_assessments": result.resource_value_assessments or [],
        "rule_candidates": result.rule_candidates or [],
        "detection_rules": result.detection_rules or [],
        "rule_validations": result.rule_validations or [],
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
        hierarchy_issues=[],
        resource_value_assessments=[],
        rule_candidates=[],
        detection_rules=[],
        rule_validations=[],
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
        service_match = re.match(r"service\s+([A-Za-z][A-Za-z0-9_]*)\b", s)
        if service_match:
            node_ids.add(service_match.group(1))
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9_]*)\s*[\[\(\{]", s):
            node_ids.add(m.group(1))
    return node_ids


def _extract_edges(code: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    edge_re = re.compile(
        r"([A-Za-z][A-Za-z0-9_]*)(?::[LTRB])?\s*(?:-->|-\.->|==>)\s*(?:[LTRB]:)?\s*([A-Za-z][A-Za-z0-9_]*)"
    )
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
    architecture_service_re = re.compile(
        r"service\s+([A-Za-z][A-Za-z0-9_]*)\s*(?:\([^\)]*\))?\s*\[([^\]]{1,200})\]"
    )
    for m in architecture_service_re.finditer(code):
        node_id = m.group(1)
        label = m.group(2).strip().strip('"').strip("'").strip()
        if label:
            labels[node_id] = label
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


LOW_VALUE_TERMS = (
    "legend",
    "note",
    "notes",
    "example",
    "placeholder",
)

HIGH_VALUE_TERMS = (
    "internet",
    "public",
    "gateway",
    "ingress",
    "egress",
    "identity",
    "auth",
    "token",
    "key vault",
    "kms",
    "secret",
    "database",
    "storage",
    "bucket",
    "queue",
    "topic",
    "message",
)

CONTEXTUAL_TERMS = (
    "service",
    "backend",
    "frontend",
    "api",
    "app",
    "function",
    "lambda",
    "worker",
    "container",
    "cluster",
)


def classify_resource_value(resource_hint: str) -> dict[str, str]:
    text = (resource_hint or "").lower()
    if any(term in text for term in LOW_VALUE_TERMS):
        return {
            "classification": "low_value",
            "rationale": "Looks like non-architectural noise/annotation rather than a threat-path component.",
        }
    if any(term in text for term in HIGH_VALUE_TERMS):
        return {
            "classification": "high_value",
            "rationale": "Directly impacts exposure, trust boundaries, identity, or data-path threat analysis.",
        }
    if any(term in text for term in CONTEXTUAL_TERMS):
        return {
            "classification": "contextual",
            "rationale": "Provides service-flow context even if not a primary exposure/data boundary node.",
        }
    # Broad default selected by user: include most resources unless obvious noise.
    return {
        "classification": "contextual",
        "rationale": "Broad default: keep likely architecture context unless clearly noisy.",
    }


def annotate_issue_with_value(issue: dict[str, Any]) -> dict[str, Any]:
    hint = " ".join(
        [
            str(issue.get("node_id") or ""),
            str(issue.get("expected_asset") or ""),
            str(issue.get("description") or ""),
            str(issue.get("diagram_title") or ""),
        ]
    )
    value = classify_resource_value(hint)
    issue["value_assessment"] = value
    issue["element_rationale"] = build_element_rationale(issue, value)
    return issue


def build_element_rationale(issue: dict[str, Any], value: dict[str, str]) -> dict[str, str]:
    """Attach security-architect rationale for why an element should exist."""
    issue_type = str(issue.get("issue_type") or "unknown")
    resource_hint = (
        str(issue.get("node_id") or issue.get("expected_asset") or issue_type).replace("_", " ").strip()
        or "element"
    )
    classification = value.get("classification", "contextual")
    contribution_map = {
        "high_value": "Directly participates in threat-model boundaries (ingress, identity, data, or trust).",
        "contextual": "Provides architectural path context needed to reason about attacker movement.",
        "low_value": "Likely non-security noise unless evidence proves it affects a real attack path.",
    }
    smell_map = {
        "orphan_node": "Unconnected nodes are a strong smell that detection logic is incomplete or relationships are missing.",
        "flat_hierarchy_smell": "Flat child resources weaken threat-model accuracy because parent controls are obscured.",
        "missing_internet_to_ingress": "Missing ingress linkage hides internet attack surface and entry point controls.",
        "missing_ingress_to_service": "Missing gateway-to-service linkage hides enforcement boundaries and bypass risk.",
        "missing_service_to_data": "Missing service-to-data linkage hides data-exfiltration and privilege escalation paths.",
        "docs_iac_parity_gap": "Parity gaps imply detection rules missed evidence-backed architecture elements.",
    }
    return {
        "why_present_question": f"Why is '{resource_hint}' present, and what threat-model boundary or path does it represent?",
        "security_contribution": contribution_map.get(
            classification,
            "Contributes context for threat-model completeness and attack-path reasoning.",
        ),
        "smell_if_unconnected": smell_map.get(
            issue_type,
            "Missing or unconnected representation indicates possible detection-rule or parsing coverage gaps.",
        ),
    }


def _extract_subgraph_hierarchy(code: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Parse Mermaid subgraph context for simple hierarchy checks.

    Returns:
        node_to_subgraphs: node_id -> list of current subgraph labels (inner-most last)
        subgraph_labels: subgraph_id -> normalized label text
    """
    node_to_subgraphs: dict[str, list[str]] = {}
    subgraph_labels: dict[str, str] = {}
    stack: list[str] = []

    subgraph_with_label_re = re.compile(r'^\s*subgraph\s+([A-Za-z][A-Za-z0-9_]*)\s*\["([^"]+)"\]\s*$')
    subgraph_plain_re = re.compile(r'^\s*subgraph\s+([A-Za-z][A-Za-z0-9_]*)\s*$')
    node_decl_re = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\[\[|\[\(|\[|\(\[|\(\"|\(\(|\{)")

    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        with_label = subgraph_with_label_re.match(line)
        if with_label:
            subgraph_id = with_label.group(1)
            label = with_label.group(2).strip().lower()
            subgraph_labels[subgraph_id] = label
            node_to_subgraphs[subgraph_id] = list(stack)
            stack.append(label)
            continue

        plain = subgraph_plain_re.match(line)
        if plain:
            subgraph_id = plain.group(1)
            label = subgraph_id.strip().lower()
            subgraph_labels[subgraph_id] = label
            node_to_subgraphs[subgraph_id] = list(stack)
            stack.append(label)
            continue

        if line.lower() == "end":
            if stack:
                stack.pop()
            continue

        if line.lower().startswith(("flowchart", "architecture-beta", "classdef", "class ", "style ", "linkstyle")):
            continue
        if "-->" in line or "-.->" in line or "==>" in line:
            continue

        for m in node_decl_re.finditer(line):
            node_id = m.group(1)
            node_to_subgraphs[node_id] = list(stack)

    return node_to_subgraphs, subgraph_labels


def _extract_node_to_subgraph_ids(code: str) -> dict[str, list[str]]:
    """Parse Mermaid node -> enclosing subgraph IDs (inner-most last)."""
    node_to_subgraph_ids: dict[str, list[str]] = {}
    stack: list[str] = []
    subgraph_with_label_re = re.compile(r'^\s*subgraph\s+([A-Za-z][A-Za-z0-9_]*)\s*\["([^"]+)"\]\s*$')
    subgraph_plain_re = re.compile(r'^\s*subgraph\s+([A-Za-z][A-Za-z0-9_]*)\s*$')
    node_decl_re = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\[\[|\[\(|\[|\(\[|\(\"|\(\(|\{)")

    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        with_label = subgraph_with_label_re.match(line)
        if with_label:
            subgraph_id = with_label.group(1)
            node_to_subgraph_ids[subgraph_id] = list(stack)
            stack.append(subgraph_id)
            continue

        plain = subgraph_plain_re.match(line)
        if plain:
            subgraph_id = plain.group(1)
            node_to_subgraph_ids[subgraph_id] = list(stack)
            stack.append(subgraph_id)
            continue

        if line.lower() == "end":
            if stack:
                stack.pop()
            continue

        if line.lower().startswith(("flowchart", "architecture-beta", "classdef", "class ", "style ", "linkstyle")):
            continue
        if "-->" in line or "-.->" in line or "==>" in line:
            continue

        for m in node_decl_re.finditer(line):
            node_id = m.group(1)
            node_to_subgraph_ids[node_id] = list(stack)
    return node_to_subgraph_ids


def detect_hierarchy_issues(code: str, provider: str, diagram_title: str) -> list[dict[str, Any]]:
    """Detect likely flat child resources that should be nested under parent subgraphs."""
    labels = _extract_node_label_map(code)
    node_to_subgraphs, _subgraph_labels = _extract_subgraph_hierarchy(code)
    all_label_text = " ".join((label or "").lower() for label in labels.values())
    all_subgraph_text = " ".join(" ".join(v) for v in node_to_subgraphs.values()).lower()
    searchable = f"{all_label_text} {all_subgraph_text}"

    # child_terms -> parent_terms
    hierarchy_rules = [
        (("container", "bucket object", "blob"), ("storage account", "bucket", "storage")),
        (("database", "sql database"), ("sql server", "postgres server", "mysql server", "database instance", "cosmos")),
        (("api", "operation"), ("api management", "apim", "api gateway")),
        (("queue", "subscription"), ("service bus", "topic", "namespace", "pubsub")),
    ]

    issues: list[dict[str, Any]] = []
    for node_id, label in labels.items():
        label_l = (label or "").lower()
        if not label_l:
            continue

        for child_terms, parent_terms in hierarchy_rules:
            if not any(term in label_l for term in child_terms):
                continue
            if not any(term in searchable for term in parent_terms):
                continue
            if any(term in label_l for term in parent_terms):
                continue

            enclosing = " ".join(node_to_subgraphs.get(node_id, [])).lower()
            if any(term in enclosing for term in parent_terms):
                continue

            issues.append(
                {
                    "issue_type": "flat_hierarchy_smell",
                    "diagram_title": diagram_title,
                    "provider": provider,
                    "node_id": node_id,
                    "description": (
                        f"Node '{node_id}' looks like a child resource but is not nested under an expected parent subgraph."
                    ),
                    "repo_evidence_files": [],
                    "provider_default_refs": PROVIDER_DEFAULT_REFERENCES.get(provider, []),
                }
            )
            break

    return issues


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
        if any(term in text for term in ("service", "backend", "application", "function", "lambda", "container", "app"))
        and not any(term in text for term in ("gateway", "ingress", "load balancer", "front door", "apim"))
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
    node_to_subgraph_ids = _extract_node_to_subgraph_ids(code)
    all_nodes = set(nodes) | set(node_to_subgraph_ids.keys())
    if not nodes:
        return []
    degree: dict[str, int] = {n: 0 for n in all_nodes}
    for src, dst in edges:
        if src in degree:
            degree[src] += 1
        if dst in degree:
            degree[dst] += 1
    orphans = [
        node
        for node, d in degree.items()
        if node in nodes
        and d == 0
        and not any(degree.get(parent, 0) > 0 for parent in node_to_subgraph_ids.get(node, []))
        and node.lower() not in IGNORED_ORPHAN_NODE_IDS
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


def write_detection_rules(
    repo: RepoOption,
    issues: list[dict[str, Any]],
    rules_dir: Path = REPO_ROOT / "Rules" / "Detection",
) -> list[dict[str, Any]]:
    """Write concrete detection rules for evidence-backed diagram coverage gaps.
    
    Prefers existing proper context-* rules over generating simple pattern rules.
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    
    # Map asset types to existing context detection rules
    # Format: "asset-name" -> ("provider", "rule-file-name-in-provider-folder")
    ASSET_TO_CONTEXT_RULE: dict[str, tuple[str, str]] = {
        "queue": ("AWS", "sqs-queue-detection"),
        "sqs": ("AWS", "sqs-queue-detection"),
        "sqs-queue": ("AWS", "sqs-queue-detection"),
    }
    
    for issue in issues:
        evidence_files = issue.get("repo_evidence_files") or []
        if not evidence_files:
            continue
        provider = issue.get("provider") or "unknown"
        issue_type = issue.get("issue_type") or "coverage_gap"
        issue_key = issue.get("node_id") or issue.get("expected_asset") or issue_type
        clean_node = _slug(str(issue_key).lower())
        
        # Check if a proper context rule exists for this asset
        if clean_node in ASSET_TO_CONTEXT_RULE:
            provider_name, rule_filename = ASSET_TO_CONTEXT_RULE[clean_node]
            context_rule_file = rules_dir / provider_name / f"{rule_filename}.yml"
            if context_rule_file.exists():
                # Extract rule_id from the file
                try:
                    content = context_rule_file.read_text()
                    # Parse YAML to get rule ID (simple regex for now)
                    match = re.search(r'id:\s*([^\n]+)', content)
                    context_rule_id = match.group(1).strip() if match else rule_filename
                except Exception:
                    context_rule_id = rule_filename
                
                written.append(
                    {
                        "rule_id": context_rule_id,
                        "rule_file": str(context_rule_file),
                        "issue_type": issue_type,
                        "source_issue_key": str(issue_key),
                        "repo_name": repo.name,
                        "validate_command": f"opengrep scan --config {context_rule_file} {repo.path}",
                        "note": "Using existing context-detection rule instead of generated placeholder",
                    }
                )
                continue
        
        # Fall back to generating a simple pattern rule (for unmapped assets)
        rule_id = f"{provider}-{clean_node}-detection"
        pattern_regex = rf"(?i){re.escape(str(issue_key).replace('_', ' '))}"
        rule_file = rules_dir / f"{rule_id}.yml"
        content = "\n".join(
            [
                "rules:",
                f"  - id: {rule_id}",
                "    message: |",
                f"      Diagram coverage gap detected for '{issue_key}' in repo '{repo.name}'.",
                "      Resource appears in repo evidence but diagram is missing a required connection or representation.",
                "    severity: WARNING",
                "    languages: [regex]",
                "    patterns:",
                f"      - pattern-regex: '{pattern_regex}'",
                "    metadata:",
                "      category: security",
                "      subcategory: [asset-discovery-gap]",
                f"      technology: [{provider}]",
                "      finding_kind: asset_detection",
                "      generated_by: web_parallel_scan_validator",
                "      note: placeholder_rule - consider mapping to existing context-detection rule",
            ]
        ) + "\n"
        rule_file.write_text(content, encoding="utf-8")
        written.append(
            {
                "rule_id": rule_id,
                "rule_file": str(rule_file),
                "issue_type": issue_type,
                "source_issue_key": str(issue_key),
                "repo_name": repo.name,
                "validate_command": f"opengrep scan --config {rule_file} {repo.path}",
                "note": "Generated placeholder rule (consider adding to ASSET_TO_CONTEXT_RULE mapping)",
            }
        )
    return written


def validate_rules_with_opengrep(
    rules: list[dict[str, Any]],
    repo_path: str,
    timeout_sec: int,
) -> list[dict[str, Any]]:
    """Validate generated rules with opengrep scan --config <rule> <repo>."""
    validations: list[dict[str, Any]] = []
    opengrep_bin = shutil.which("opengrep")
    for rule in rules:
        rule_file = rule.get("rule_file")
        command = ["opengrep", "scan", "--config", str(rule_file), str(repo_path)]
        if not opengrep_bin:
            validations.append(
                {
                    "rule_id": rule.get("rule_id"),
                    "rule_file": rule_file,
                    "status": "failed",
                    "exit_code": None,
                    "error": "opengrep binary not found on PATH",
                    "command": " ".join(command),
                }
            )
            continue
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            validations.append(
                {
                    "rule_id": rule.get("rule_id"),
                    "rule_file": rule_file,
                    "status": "passed" if proc.returncode == 0 else "failed",
                    "exit_code": proc.returncode,
                    "command": " ".join(command),
                    "stdout_tail": (proc.stdout or "")[-1000:],
                    "stderr_tail": (proc.stderr or "")[-1000:],
                }
            )
        except subprocess.TimeoutExpired:
            validations.append(
                {
                    "rule_id": rule.get("rule_id"),
                    "rule_file": rule_file,
                    "status": "failed",
                    "exit_code": None,
                    "error": f"opengrep validation timed out after {timeout_sec}s",
                    "command": " ".join(command),
                }
            )
    return validations


async def discover_repos(page) -> list[RepoOption]:
    await page.goto("/", wait_until="domcontentloaded")
    await page.wait_for_selector("#repo-select", state="attached")
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
    await page.wait_for_selector("#repo-select", state="attached")
    set_ok = await page.evaluate(
        """
        (repoPath) => {
          const sel = document.querySelector('#repo-select');
          if (!sel) return false;
          const hasOption = Array.from(sel.options || []).some((opt) => opt.value === repoPath);
          sel.value = repoPath;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
          return hasOption;
        }
        """,
        repo.path,
    )
    if not set_ok:
        raise RuntimeError(f"{repo.name}: repo path not found in #repo-select options")
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


async def get_scan_history_count(page, repo_status_key: str) -> int:
    """Return number of scan history rows currently recorded for a repo."""
    escaped = quote(repo_status_key, safe="")
    response = await page.request.get(f"/api/scans/{escaped}")
    if not response.ok:
        raise RuntimeError(
            f"Failed to read scan history for {repo_status_key}: HTTP {response.status}"
        )
    payload = await response.json()
    scans = payload.get("scans") or []
    return len(scans)


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    escaped_repo = quote(repo.api_key, safe="")
    response = await page.request.get(f"/api/diagrams/{experiment_id}?repo_name={escaped_repo}")
    if not response.ok:
        raise RuntimeError(f"{repo.name}: failed to load diagrams payload ({response.status})")
    payload = await response.json()
    diagrams = payload.get("diagrams") or []
    orphan_issues: list[dict[str, Any]] = []
    connection_issues: list[dict[str, Any]] = []
    parity_issues: list[dict[str, Any]] = []
    hierarchy_issues: list[dict[str, Any]] = []
    resource_value_assessments: list[dict[str, Any]] = []
    repo_root = Path(repo.path)
    expected_assets = infer_expected_assets(repo_root) if repo_root.is_dir() else {}
    for d in diagrams:
        title = d.get("title") or "Untitled diagram"
        provider = _parse_provider_from_title(title)
        code = d.get("code") or ""
        orphans = find_orphan_nodes(code)
        
        # Add rendering validation if available
        rendering_gaps = {}
        mapping_errors = {}
        if validate_rendering_pipeline:
            orphan_diagnosis = validate_rendering_pipeline(orphans, code, provider)
            for node_id, diagnosis in orphan_diagnosis.items():
                if diagnosis.root_cause == "RENDERING_GAP":
                    rendering_gaps[node_id] = diagnosis
                elif diagnosis.root_cause == "MAPPING_ERROR":
                    mapping_errors[node_id] = diagnosis
        
        for orphan in orphans:
            evidence = gather_repo_evidence(repo_root, orphan) if repo_root.is_dir() else []
            refs = PROVIDER_DEFAULT_REFERENCES.get(provider, [])
            
            # Determine if orphan is rendering gap or mapping error
            root_cause = "REAL_ORPHAN"
            if orphan in rendering_gaps:
                root_cause = "RENDERING_GAP"
            elif orphan in mapping_errors:
                root_cause = "MAPPING_ERROR"
            
            issue = {
                "issue_type": "orphan_node",
                "diagram_title": title,
                "provider": provider,
                "node_id": orphan,
                "root_cause": root_cause,
                "repo_evidence_files": evidence,
                "provider_default_refs": refs,
            }
            orphan_issues.append(annotate_issue_with_value(issue))
            resource_value_assessments.append(
                {
                    "issue_type": "orphan_node",
                    "resource_hint": orphan,
                    "diagram_title": title,
                    "provider": provider,
                    "value_assessment": orphan_issues[-1]["value_assessment"],
                }
            )
            await logger.warn(
                f"{repo.name}: orphan node '{orphan}' in '{title}' "
                f"(evidence_files={len(evidence)}, provider_refs={len(refs)})"
            )
        missing_connections = detect_missing_connections(code, provider, title)
        for issue in missing_connections:
            connection_issues.append(annotate_issue_with_value(issue))
            resource_value_assessments.append(
                {
                    "issue_type": issue.get("issue_type"),
                    "resource_hint": issue.get("issue_type"),
                    "diagram_title": title,
                    "provider": provider,
                    "value_assessment": connection_issues[-1]["value_assessment"],
                }
            )
        for issue in missing_connections:
            await logger.warn(f"{repo.name}: {issue['issue_type']} in '{title}'")

        parity = detect_docs_iac_parity_issues(
            code=code,
            provider=provider,
            diagram_title=title,
            expected_assets=expected_assets,
        )
        for issue in parity:
            parity_issues.append(annotate_issue_with_value(issue))
            resource_value_assessments.append(
                {
                    "issue_type": issue.get("issue_type"),
                    "resource_hint": issue.get("expected_asset"),
                    "diagram_title": title,
                    "provider": provider,
                    "value_assessment": parity_issues[-1]["value_assessment"],
                }
            )
        for issue in parity:
            await logger.warn(
                f"{repo.name}: docs+IaC parity gap '{issue.get('expected_asset', 'unknown')}' in '{title}' "
                f"(evidence_files={len(issue.get('repo_evidence_files') or [])})"
            )
        hierarchy = detect_hierarchy_issues(code=code, provider=provider, diagram_title=title)
        for issue in hierarchy:
            hierarchy_issues.append(annotate_issue_with_value(issue))
            resource_value_assessments.append(
                {
                    "issue_type": issue.get("issue_type"),
                    "resource_hint": issue.get("node_id"),
                    "diagram_title": title,
                    "provider": provider,
                    "value_assessment": hierarchy_issues[-1]["value_assessment"],
                }
            )
        for issue in hierarchy:
            await logger.warn(
                f"{repo.name}: hierarchy smell '{issue.get('node_id', 'unknown')}' in '{title}' "
                "(child appears unnested)"
            )
    return orphan_issues, connection_issues, parity_issues, hierarchy_issues, resource_value_assessments


async def scan_repo_worker(
    browser,
    base_url: str,
    repo: RepoOption,
    screenshots_dir: Path,
    candidate_rules_dir: Path,
    write_rule_candidates: bool,
    write_detection_rules_enabled: bool,
    validate_detection_rules_enabled: bool,
    opengrep_timeout_sec: int,
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
        orphan_issues, connection_issues, parity_issues, hierarchy_issues, resource_value_assessments = await collect_diagram_validation_issues(
            page,
            repo,
            experiment_id,
            logger,
        )
        rule_candidates: list[dict[str, Any]] = []
        detection_rules: list[dict[str, Any]] = []
        rule_validations: list[dict[str, Any]] = []
        candidate_input = [*orphan_issues, *connection_issues, *parity_issues, *hierarchy_issues]
        if write_rule_candidates and candidate_input:
            rule_candidates = write_rule_candidate_stubs(repo, candidate_input, candidate_rules_dir)
            if rule_candidates:
                await logger.improvement(
                    f"{repo.name}: wrote {len(rule_candidates)} candidate detection rules"
                )
        if write_detection_rules_enabled and candidate_input:
            detection_rules = write_detection_rules(repo, candidate_input)
            if detection_rules:
                await logger.improvement(
                    f"{repo.name}: wrote {len(detection_rules)} detection rule(s) to Rules/Detection"
                )
            if validate_detection_rules_enabled and detection_rules:
                rule_validations = validate_rules_with_opengrep(
                    detection_rules,
                    repo.path,
                    timeout_sec=opengrep_timeout_sec,
                )
                passed = sum(1 for r in rule_validations if r.get("status") == "passed")
                failed = len(rule_validations) - passed
                await logger.improvement(
                    f"{repo.name}: opengrep validation complete for generated rules (passed={passed}, failed={failed})"
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
            hierarchy_issues=hierarchy_issues,
            resource_value_assessments=resource_value_assessments,
            rule_candidates=rule_candidates,
            detection_rules=detection_rules,
            rule_validations=rule_validations,
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
            hierarchy_issues=[],
            resource_value_assessments=[],
            rule_candidates=[],
            detection_rules=[],
            rule_validations=[],
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
            hierarchy_issues=[],
            resource_value_assessments=[],
            rule_candidates=[],
            detection_rules=[],
            rule_validations=[],
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
    worker_concurrency = 1 if args.repo_at_a_time else effective_concurrency(args.mode, args.concurrency)
    await logger.info(f"Mode: {args.mode}")
    await logger.info(f"Repo-at-a-time: {bool(args.repo_at_a_time)}")
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
    skipped_scanned_repos: list[RepoOption] = []
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

            # If caller supplied explicit --repos, avoid UI dropdown discovery and
            # resolve directly from Intake/search roots to reduce UI dependency.
            if args.repos:
                wanted = [r.strip() for r in args.repos if r.strip()]
                wanted_set = {r.lower() for r in wanted}
                intake_by_name = {r.name.lower(): r for r in intake_resolved}
                repos = [intake_by_name[name] for name in wanted_set if name in intake_by_name]
                missing = sorted({name for name in wanted_set if name not in intake_by_name})
                dropdown_repos = list(intake_resolved)
                if missing:
                    await logger.warn(
                        f"Requested repos not resolvable from Intake/search roots and will be skipped: {missing}"
                    )
                await logger.info(f"Repo filter applied: {sorted(wanted_set)}")
            else:
                dropdown_repos = await discover_repos(page)
                repos, skipped_dropdown_repos = filter_repos_to_resolved_intake(dropdown_repos, intake_resolved)
                if skipped_dropdown_repos:
                    await logger.warn(
                        f"Skipping {len(skipped_dropdown_repos)} dropdown repo(s) not resolvable via Intake/search paths: "
                        f"{[r.name for r in skipped_dropdown_repos]}"
                    )

            repos = partition_repos(repos, args.partition_count, args.partition_index)
            if args.partition_count is not None:
                await logger.info(
                    f"Partition filter applied: index={args.partition_index}/{args.partition_count - 1}, "
                    f"targeting {len(repos)} repo(s)"
                )

            if args.only_unscanned and repos:
                unscanned: list[RepoOption] = []
                for repo in repos:
                    history_count = await get_scan_history_count(page, repo.api_key)
                    if history_count == 0:
                        unscanned.append(repo)
                    else:
                        skipped_scanned_repos.append(repo)
                repos = unscanned
                await logger.info(
                    f"Only-unscanned filter applied: targeting {len(repos)} repo(s), "
                    f"skipping {len(skipped_scanned_repos)} already scanned repo(s)"
                )
                if skipped_scanned_repos:
                    await logger.warn(
                        f"Skipped already scanned repos: {[r.name for r in skipped_scanned_repos]}"
                    )

            await context.close()

            if not repos:
                await logger.error("No repositories available from UI dropdown after filtering")
                run_error = "No repositories available from UI dropdown after filtering"
            else:
                await logger.info(
                    f"Discovered {len(dropdown_repos)} repositories from dropdown, targeting {len(repos)} resolved repo(s)"
                )
                semaphore = asyncio.Semaphore(worker_concurrency)
                worker_timeout_sec = args.scan_complete_timeout_sec + WORKER_TIMEOUT_GRACE_SEC

                async def _run_one(repo: RepoOption, pass_name: str) -> ScanResult:
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
                                    write_detection_rules_enabled=bool(args.write_detection_rules),
                                    validate_detection_rules_enabled=bool(args.validate_detection_rules),
                                    opengrep_timeout_sec=args.opengrep_timeout_sec,
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
                        if result.status == "completed":
                            await logger.info(f"{repo.name}: {pass_name} pass ended with status=completed")
                        else:
                            await logger.warn(
                                f"{repo.name}: {pass_name} pass ended with status=failed; "
                                "batch will continue without stalling"
                            )
                        return result

                async def execute_pass(pass_name: str, target_repos: list[RepoOption]) -> list[ScanResult]:
                    await logger.info(f"Starting {pass_name} pass for {len(target_repos)} repo(s)")
                    pass_results: list[ScanResult] = []
                    if args.mode == "serial":
                        for repo in target_repos:
                            pass_results.append(await _run_one(repo, pass_name))
                    else:
                        tasks = [asyncio.create_task(_run_one(repo, pass_name)) for repo in target_repos]
                        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                        for repo, outcome in zip(target_repos, outcomes):
                            if isinstance(outcome, Exception):
                                await logger.error(f"{repo.name}: worker crashed unexpectedly: {outcome}")
                                pass_results.append(make_failed_result(repo, str(outcome)))
                            else:
                                pass_results.append(outcome)
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

                if args.repo_at_a_time:
                    await logger.info("Repo-at-a-time mode enabled: strict serial execution with immediate retry")
                    for repo in repos:
                        primary_result = await _run_one(repo, "primary")
                        primary_results.append(primary_result)
                        if primary_result.status != "completed":
                            await logger.warn(f"{repo.name}: retrying immediately before continuing to next repo")
                            retry_results.append(await _run_one(repo, "retry"))
                else:
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
        "repo_at_a_time": bool(args.repo_at_a_time),
        "concurrency": args.concurrency,
        "effective_concurrency": worker_concurrency,
        "scan_complete_timeout_sec": args.scan_complete_timeout_sec,
        "write_detection_rules": bool(args.write_detection_rules),
        "validate_detection_rules": bool(args.validate_detection_rules),
        "opengrep_timeout_sec": args.opengrep_timeout_sec,
        "partition_count": args.partition_count,
        "partition_index": args.partition_index,
        "only_unscanned": bool(args.only_unscanned),
        "repos_discovered_from_dropdown": len(dropdown_repos),
        "repos_resolved_from_intake": len(intake_resolved),
        "repos_unresolved_from_intake": intake_unresolved,
        "repos_skipped_from_dropdown": [
            {"name": r.name, "path": r.path} for r in sorted(skipped_dropdown_repos, key=lambda x: x.name.lower())
        ],
        "repos_skipped_already_scanned": [
            {"name": r.name, "path": r.path} for r in sorted(skipped_scanned_repos, key=lambda x: x.name.lower())
        ],
        "repos_total": len(repos),
        "primary_completed": sum(1 for r in primary_results if r.status == "completed"),
        "primary_failed": sum(1 for r in primary_results if r.status != "completed"),
        "retry_attempted": len(retry_results),
        "retry_recovered": sum(1 for r in retry_results if r.status == "completed"),
        "detection_rules_written": sum(len(r.detection_rules or []) for r in final_results),
        "detection_rules_validated": sum(len(r.rule_validations or []) for r in final_results),
        "detection_rule_validation_failed": sum(
            1
            for r in final_results
            for v in (r.rule_validations or [])
            if v.get("status") != "passed"
        ),
        "hierarchy_issues_total": sum(len(r.hierarchy_issues or []) for r in final_results),
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
        "--repo-at-a-time",
        action="store_true",
        help="Force strict one-repo-at-a-time execution with immediate retry on failure.",
    )
    parser.add_argument(
        "--mode",
        choices=("parallel", "serial"),
        default="parallel",
        help="Execution mode: parallel (default) or serial one-by-one.",
    )
    parser.add_argument("--audit-root", default=str(DEFAULT_AUDIT_ROOT), help="Audit output root directory")
    parser.add_argument("--repos", nargs="*", help="Optional repo names to include (defaults to all dropdown repos)")
    parser.add_argument(
        "--only-unscanned",
        action="store_true",
        help="Scan only repos with zero prior scan history from /api/scans/<repo>.",
    )
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
    parser.add_argument(
        "--write-detection-rules",
        action="store_true",
        help="Write evidence-backed detection rules under Rules/Detection/ for confirmed diagram gaps.",
    )
    parser.add_argument(
        "--validate-detection-rules",
        action="store_true",
        help="Validate generated Rules/Detection rules with opengrep scan --config <rule> <repo>.",
    )
    parser.add_argument(
        "--opengrep-timeout-sec",
        type=int,
        default=120,
        help="Timeout in seconds for each generated rule opengrep validation run (default: 120).",
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
    if args.opengrep_timeout_sec < 1:
        print("--opengrep-timeout-sec must be >= 1", flush=True)
        return 2
    if args.validate_detection_rules and not args.write_detection_rules:
        print("--validate-detection-rules requires --write-detection-rules", flush=True)
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
