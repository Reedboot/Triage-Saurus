#!/usr/bin/env python3
"""Pre-scan remote sync check — fetch and optionally pull a local git repository.

Implements the "Pre-Scan Remote Sync Check" workflow from Agents/RepoAgent.md so
agents can call a single script instead of embedding raw shell commands.

Usage:
  python3 Scripts/pull_repo.py /abs/path/to/repo
  python3 Scripts/pull_repo.py /abs/path/to/repo --auto-pull
  python3 Scripts/pull_repo.py /abs/path/to/repo --dry-run

Exit codes:
  0  up-to-date or pull succeeded
  1  runtime error (not a repo, git unavailable, pull failed)
  2  usage error
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Git helpers (all use subprocess; no external dependencies)
# ---------------------------------------------------------------------------

def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_git_repo(repo: Path) -> bool:
    rc, _, _ = _run(["git", "rev-parse", "--git-dir"], repo)
    return rc == 0


def _current_branch(repo: Path) -> str:
    _, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    return out


def _has_upstream(repo: Path) -> bool:
    rc, _, _ = _run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], repo)
    return rc == 0


def _upstream_branch(repo: Path) -> str:
    _, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], repo)
    return out


def _has_uncommitted_changes(repo: Path) -> bool:
    rc, _, _ = _run(["git", "diff-index", "--quiet", "HEAD", "--"], repo)
    return rc != 0


def _rev_parse(repo: Path, ref: str) -> str:
    rc, out, err = _run(["git", "rev-parse", ref], repo)
    if rc != 0:
        raise RuntimeError(f"git rev-parse {ref!r} failed: {err}")
    return out


def _count_commits(repo: Path, range_spec: str) -> int:
    """Return number of commits in range_spec (e.g. 'HEAD..@{upstream}')."""
    rc, out, err = _run(["git", "rev-list", "--count", range_spec], repo)
    if rc != 0:
        print(f"⚠️  git rev-list failed for {range_spec!r}: {err}", file=sys.stderr)
        return 0
    try:
        return int(out)
    except ValueError:
        print(f"⚠️  Unexpected output from git rev-list: {out!r}", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# Core workflow
# ---------------------------------------------------------------------------

def check_remote_status(repo: Path) -> dict:
    """Fetch and compare local vs remote. Returns a status dict."""
    # Fetch remote refs
    rc, _, err = _run(["git", "fetch", "origin", "--quiet"], repo)
    if rc != 0:
        return {"error": f"git fetch failed: {err}"}

    branch = _current_branch(repo)

    if not _has_upstream(repo):
        return {
            "branch": branch,
            "status": "no_upstream",
            "message": f"⚠️  No upstream branch configured. Local branch: {branch}",
        }

    upstream = _upstream_branch(repo)
    try:
        local_commit = _rev_parse(repo, "HEAD")
        remote_commit = _rev_parse(repo, "@{upstream}")
    except RuntimeError as exc:
        return {"error": str(exc)}

    if local_commit == remote_commit:
        return {
            "branch": branch,
            "upstream": upstream,
            "local_commit": local_commit,
            "remote_commit": remote_commit,
            "status": "up_to_date",
            "ahead": 0,
            "behind": 0,
            "message": f"✅ Repository is up-to-date with {upstream}",
        }

    ahead = _count_commits(repo, "@{upstream}..HEAD")
    behind = _count_commits(repo, "HEAD..@{upstream}")

    if behind > 0 and ahead == 0:
        status = "behind"
        message = (
            f"⚠️  Repository is BEHIND remote by {behind} commit(s).\n"
            f"   Remote has newer changes that aren't in your local copy."
        )
    elif ahead > 0 and behind == 0:
        status = "ahead"
        message = (
            f"ℹ️  Repository is AHEAD of remote by {ahead} commit(s).\n"
            f"   You have local commits not yet pushed to remote."
        )
    else:
        status = "diverged"
        message = (
            f"⚠️  Repository has DIVERGED from remote.\n"
            f"   Local is ahead by {ahead}, behind by {behind} commit(s)."
        )

    return {
        "branch": branch,
        "upstream": upstream,
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "status": status,
        "ahead": ahead,
        "behind": behind,
        "message": message,
    }


def pull_latest(repo: Path, branch: str) -> bool:
    """Stash uncommitted changes (if any), pull, then restore. Returns True on success."""
    stashed = False

    if _has_uncommitted_changes(repo):
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        stash_msg = f"Auto-stash before security scan {stamp}"
        print(f"⚠️  Uncommitted changes detected. Stashing: {stash_msg!r}")
        rc, _, err = _run(["git", "stash", "save", stash_msg], repo)
        if rc != 0:
            print(f"❌ git stash failed: {err}", file=sys.stderr)
            return False
        stashed = True

    rc, out, err = _run(["git", "pull", "origin", branch], repo)
    if rc != 0:
        print(f"❌ git pull failed: {err}", file=sys.stderr)
        if stashed:
            print("Restoring stashed changes...")
            _run(["git", "stash", "pop"], repo)
        return False

    if out:
        print(out)

    if stashed:
        print("Restoring stashed changes...")
        rc2, _, err2 = _run(["git", "stash", "pop"], repo)
        if rc2 != 0:
            print(f"⚠️  git stash pop failed: {err2}", file=sys.stderr)

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-scan remote sync check: fetch and optionally pull a local git repo.",
    )
    parser.add_argument("repo", help="Absolute or relative path to the local git repository.")
    parser.add_argument(
        "--auto-pull",
        action="store_true",
        help="Automatically pull if the repo is behind remote (no interactive prompt).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report status only; do not pull.",
    )

    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()

    if not repo.is_dir():
        print(f"❌ ERROR: path not found or not a directory: {repo}", file=sys.stderr)
        return 2

    if not _is_git_repo(repo):
        print(f"❌ ERROR: not a git repository: {repo}", file=sys.stderr)
        return 1

    print(f"== Pre-scan remote sync check ==")
    print(f"Repository: {repo}")
    print()

    status = check_remote_status(repo)

    if "error" in status:
        print(f"❌ {status['error']}", file=sys.stderr)
        return 1

    print(status["message"])
    print()

    sync_status = status["status"]

    if sync_status == "up_to_date":
        print(f"Local commit:  {status['local_commit'][:12]}")
        print(f"Remote commit: {status['remote_commit'][:12]}")
        return 0

    if sync_status == "no_upstream":
        print("Proceeding with local branch (no upstream to compare).")
        return 0

    if sync_status == "ahead":
        print("Proceeding with local branch (scanning unpushed work-in-progress).")
        print(f"Local commit: {status['local_commit'][:12]}")
        return 0

    # behind or diverged — ask or auto-pull
    print(f"Branch:        {status.get('branch', 'unknown')}")
    print(f"Upstream:      {status.get('upstream', 'unknown')}")
    print(f"Local commit:  {status['local_commit'][:12]}")
    print(f"Remote commit: {status['remote_commit'][:12]}")
    print()

    if args.dry_run:
        print("Dry-run mode: not pulling. Pass --auto-pull to pull automatically.")
        return 0

    if args.auto_pull:
        do_pull = True
    else:
        print("Options:")
        print("  1. Pull latest and proceed (Recommended)")
        print("  2. Scan current local version")
        print("  3. Cancel")
        choice = input("Choice [1/2/3]: ").strip()
        do_pull = choice == "1"
        if choice == "3":
            print("Scan cancelled.")
            return 0
        if choice not in ("1", "2"):
            print(f"Unrecognized choice {choice!r}. Proceeding without pull.", file=sys.stderr)
            do_pull = False

    if do_pull:
        branch = status.get("branch", "main")
        print(f"Pulling origin/{branch}...")
        if not pull_latest(repo, branch):
            return 1
        new_commit = _rev_parse(repo, "HEAD")
        print(f"✅ Repository updated to latest. New HEAD: {new_commit[:12]}")
    else:
        print("Proceeding with current local version (may be outdated).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
