#!/usr/bin/env python3
"""
Run opengrep scans in manageable chunks to avoid CLI hangs on large repositories.

This script groups tracked files under the target directory into batches (by
default 8 subpaths / ~800 files) and executes separate `opengrep scan`
invocations per batch while preserving full rule coverage.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class PathSummary:
    rel_path: str
    file_count: int


def run_git_ls_files(
    repo_path: Path, rel_path: str | None = None, exclude_patterns: List[str] | None = None
) -> int:
    """Return the count of git-tracked files under rel_path (or all files if None).

    Files whose path contains any of the exclude_patterns substrings are not counted,
    mirroring the --exclude flags that will be passed to opengrep.
    """
    cmd = ["git", "-C", str(repo_path), "ls-files"]
    if rel_path:
        cmd.append(rel_path)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed for '{rel_path or '.'}': {result.stderr.strip()}"
        )
    output = result.stdout.strip()
    if not output:
        return 0
    lines = output.splitlines()
    if exclude_patterns:
        lines = [
            line for line in lines
            if not any(pat in line for pat in exclude_patterns)
        ]
    return len(lines)


def gather_path_summaries(target: Path, exclude_patterns: List[str] | None = None) -> List[PathSummary]:
    """Collect tracked file counts for immediate children of the target directory."""
    summaries: List[PathSummary] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name):
        if child.name == ".git":
            continue
        rel = child.name
        count = run_git_ls_files(target, rel, exclude_patterns)
        if count == 0:
            continue
        summaries.append(PathSummary(rel_path=rel, file_count=count))
    return summaries


def build_chunks(
    paths: List[PathSummary], max_files: int, max_paths: int
) -> List[List[str]]:
    """Group relative paths into chunks constrained by file and path counts."""
    chunks: List[List[str]] = []
    current: List[str] = []
    current_files = 0

    def flush():
        nonlocal current, current_files
        if current:
            chunks.append(current)
            current = []
            current_files = 0

    for summary in paths:
        if summary.file_count == 0:
            continue
        # If current chunk cannot accommodate this path, flush first
        needs_flush = (
            current
            and (
                len(current) >= max_paths
                or current_files + summary.file_count > max_files
            )
        )
        if needs_flush:
            flush()
        # Oversized path: run it as a standalone chunk
        if summary.file_count > max_files and summary.rel_path != ".":
            flush()
            chunks.append([summary.rel_path])
            continue
        current.append(summary.rel_path)
        current_files += summary.file_count
    flush()
    return chunks or [["."]]


def run_chunk(
    chunk: List[str], args: argparse.Namespace, cwd: Path, index: int, total: int
) -> int:
    exclude_flags: List[str] = []
    for pat in getattr(args, "exclude", None) or []:
        exclude_flags += ["--exclude", pat]
    cmd = [args.opengrep, "scan", "--config", args.config, *exclude_flags, *chunk]
    print(f"[chunk {index}/{total}] {' '.join(cmd)}")
    if args.dry_run:
        return 0
    process = subprocess.run(cmd, cwd=cwd)
    return process.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run opengrep in batches to avoid hangs on large repositories."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Path to the directory to scan (e.g., /mnt/c/Repos/accounts/src)",
    )
    parser.add_argument(
        "--config",
        default="Rules/",
        help="Path to the opengrep rules directory (default: Rules/)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=800,
        help="Maximum tracked files per chunk (default: 800)",
    )
    parser.add_argument(
        "--max-paths",
        type=int,
        default=8,
        help="Maximum subpaths per chunk (default: 8)",
    )
    parser.add_argument(
        "--opengrep",
        default=os.environ.get("OPENGREP_BIN", "opengrep"),
        help="opengrep executable to invoke (default: opengrep)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        default=[],
        help=(
            "Glob pattern to exclude from scanning (passed to opengrep --exclude). "
            "Also excluded when counting files to determine chunk sizes. "
            "May be specified multiple times, e.g. --exclude '.python_packages'."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned chunks without running opengrep",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    if not target.is_dir():
        print(f"Target '{target}' is not a directory", file=sys.stderr)
        return 1
    try:
        subprocess.run(["git", "-C", str(target), "rev-parse"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print(f"Target '{target}' is not inside a git repository", file=sys.stderr)
        return 1

    summaries = gather_path_summaries(target, args.exclude or None)
    chunks = build_chunks(summaries, args.max_files, args.max_paths)
    print(
        f"Prepared {len(chunks)} opengrep chunk(s) "
        f"(max_files={args.max_files}, max_paths={args.max_paths})"
    )

    for index, chunk in enumerate(chunks, start=1):
        status = run_chunk(chunk, args, target, index, len(chunks))
        if status != 0:
            print(f"Chunk {index} failed with exit code {status}", file=sys.stderr)
            return status
    return 0


if __name__ == "__main__":
    sys.exit(main())
