#!/usr/bin/env python3
"""Repo scan kickoff helper (stdout only; no file writes).

Purpose:
- Report whether Knowledge/Repos.md exists
- Suggest a default repo root (parent of this repo)

Usage:
  python3 Scripts/repo_scan_kickoff.py

Exit codes:
  0 success
"""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    from output_paths import OUTPUT_KNOWLEDGE_DIR

    repos_md = OUTPUT_KNOWLEDGE_DIR / "Repos.md"
    suggested_root = repo_root.parent

    print("== Repo scan kickoff ==")
    print(f"workspace_repo_root: {repo_root}")
    print(f"knowledge_repos_md: {repos_md}")
    print(f"knowledge_repos_md_exists: {'yes' if repos_md.is_file() else 'no'}")
    print(f"suggested_repo_root: {suggested_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
