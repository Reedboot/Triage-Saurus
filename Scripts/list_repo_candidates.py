#!/usr/bin/env python3
"""List candidate repositories under a repos-root (stdout-only; no writes).

Purpose:
- Support the repo scan kickoff flow by enumerating repositories to scan.
- Avoid needing the user to provide a repo path up-front.

Heuristics (no git commands):
- Candidate repo = directory containing a `.git` directory/file OR common repo markers
  (e.g., package files, Terraform files) within a shallow depth.

Usage:
  python3 Scripts/list_repo_candidates.py
  python3 Scripts/list_repo_candidates.py --repos-root /abs/path/to/repos

Exit codes:
  0 success
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


REPO_MARKERS = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "docker-compose.yml",
    "Dockerfile",
}

REPO_NAME_HINTS_INFRA = (
    "terraform",
    "iac",
    "infra",
    "infrastructure",
    "platform",
    "modules",
    "bicep",
    "cloudformation",
    "pulumi",
    "kubernetes",
    "helm",
)


def _is_hidden_dir(p: Path) -> bool:
    return p.name.startswith(".")


def _looks_like_repo(dir_path: Path) -> bool:
    git_marker = dir_path / ".git"
    if git_marker.exists():
        return True

    # Shallow marker scan (depth 2)
    try:
        for root, dirs, files in os.walk(dir_path):
            rel_depth = len(Path(root).relative_to(dir_path).parts)
            if rel_depth > 2:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", ".terraform"}]

            if any(f in REPO_MARKERS for f in files):
                return True
            if any(f.endswith(".tf") for f in files):
                return True
    except OSError:
        return False

    return False


def classify_repo(name: str) -> str:
    n = name.lower()
    if any(h in n for h in REPO_NAME_HINTS_INFRA):
        return "Infrastructure (likely IaC/platform)"
    return "Application/Other"


def main() -> int:
    parser = argparse.ArgumentParser(description="List candidate repos under a repos-root (stdout-only).")
    parser.add_argument(
        "--repos-root",
        default=None,
        help="Root folder containing repositories. Default: parent of this workspace repo.",
    )
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[1]
    default_repos_root = workspace_root.parent
    repos_root = Path(args.repos_root).expanduser() if args.repos_root else default_repos_root
    repos_root = repos_root.resolve()

    print("== Repo candidates ==")
    print(f"repos_root: {repos_root}")
    print(f"workspace_repo_root: {workspace_root}")
    print()

    if not repos_root.is_dir():
        print("ERROR: repos_root is not a directory")
        return 0

    candidates: list[Path] = []
    try:
        for entry in sorted(repos_root.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir():
                continue
            if _is_hidden_dir(entry):
                continue
            if entry.resolve() == workspace_root.resolve():
                continue
            if _looks_like_repo(entry):
                candidates.append(entry)
    except OSError as ex:
        print(f"ERROR: cannot list repos_root: {ex}")
        return 0

    print(f"candidates: {len(candidates)}")
    for p in candidates:
        print(f"- {p.name} — {classify_repo(p.name)} — {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

