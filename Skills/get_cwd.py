#!/usr/bin/env python3
"""Report current working directory + a suggested repo-root for scans (stdout only).

Purpose:
- Provide a reliable "where am I?" signal for CLIs/agents.
- Suggest a default "repos root" directory for repo scan workflows when
  Output/Knowledge/Repos.md has not yet been configured.

Heuristics (no git commands):
- cwd = os.getcwd()
- git_root = nearest parent containing a .git directory/file (if any)
- suggested_repos_root:
  - if git_root found: parent of git_root
  - else: parent of cwd

Usage:
  python3 Skills/get_cwd.py

Exit codes:
  0 success
"""

from __future__ import annotations

import os
from pathlib import Path


def find_git_root(start: Path) -> Path | None:
    p = start
    while True:
        if (p / ".git").exists():
            return p
        if p.parent == p:
            return None
        p = p.parent


def main() -> int:
    cwd = Path(os.getcwd()).resolve()
    git_root = find_git_root(cwd)

    if git_root is not None:
        suggested_repos_root = git_root.parent
    else:
        suggested_repos_root = cwd.parent

    print("== Current directory ==")
    print(f"cwd: {cwd}")
    print(f"git_root: {git_root if git_root is not None else '(none)'}")
    print(f"suggested_repos_root: {suggested_repos_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
