#!/usr/bin/env python3
"""
repo_resolver.py — Centralized repo path resolution using Settings/paths.json.

Usage:
    from repo_resolver import resolve_repo, get_default_repos_root
    
    # Resolve a repo name to absolute path
    path = resolve_repo("account-viewing-permissions")
    
    # Get the default repos root
    root = get_default_repos_root()
"""

import json
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = REPO_ROOT / "Settings" / "paths.json"


def load_config() -> dict:
    """Load Settings/paths.json config."""
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    # Fallback defaults
    return {
        "repo_search_paths": [
            str(REPO_ROOT.parent),
            str(Path.home() / "repos"),
            str(Path.home() / "code"),
            str(Path.home() / "projects"),
            str(Path.home()),
        ],
        "default_repo_root": str(Path.home() / "repos"),
    }


def get_search_paths() -> list[Path]:
    """Get list of directories to search for repos (in priority order)."""
    config = load_config()
    paths = config.get("repo_search_paths", [])
    return [Path(p).expanduser().resolve() for p in paths]


def get_default_repos_root() -> Path:
    """Get the default repo root directory (first search path or configured default)."""
    config = load_config()
    
    # First try explicit default_repo_root
    default = config.get("default_repo_root")
    if default:
        path = Path(default).expanduser().resolve()
        if path.exists():
            return path
    
    # Otherwise use first search path that exists
    for search_path in get_search_paths():
        if search_path.exists():
            return search_path
    
    # Last resort: parent of Triage-Saurus
    return REPO_ROOT.parent


def resolve_repo(repo_name_or_path: str) -> Optional[Path]:
    """
    Resolve a repo name or path to an absolute path.
    
    If absolute path given, return as-is (if exists).
    If relative path or name, search in configured search paths.
    Returns None if not found.
    
    Examples:
        resolve_repo("/home/neil/repos/account-viewing-permissions") → Path(...)
        resolve_repo("account-viewing-permissions") → Path("/home/neil/repos/account-viewing-permissions")
        resolve_repo("nonexistent-repo") → None
    """
    path = Path(repo_name_or_path).expanduser()
    
    # If absolute path, just check existence
    if path.is_absolute():
        return path.resolve() if path.exists() else None
    
    # Search in configured search paths
    for search_root in get_search_paths():
        candidate = search_root / repo_name_or_path
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    
    return None


def resolve_repos(repo_names_or_paths: list[str]) -> dict[str, Optional[Path]]:
    """
    Resolve multiple repos at once.
    
    Returns dict mapping input name → resolved path (or None if not found).
    """
    return {name: resolve_repo(name) for name in repo_names_or_paths}


def list_available_repos() -> list[dict]:
    """
    Scan all search paths and return list of available repos.
    
    Returns: [{"name": "repo-name", "path": "/full/path", "location": "/search/root"}, ...]
    """
    repos = []
    seen = set()
    
    for search_path in get_search_paths():
        if not search_path.exists():
            continue
        
        try:
            for item in search_path.iterdir():
                if not item.is_dir() or item.name.startswith('.'):
                    continue
                
                # Skip if we've already seen this repo (by name)
                if item.name in seen:
                    continue
                
                seen.add(item.name)
                repos.append({
                    "name": item.name,
                    "path": str(item.resolve()),
                    "location": str(search_path),
                })
        except PermissionError:
            continue
    
    return sorted(repos, key=lambda x: x["name"])


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 repo_resolver.py <repo-name-or-path>")
        print("\nOr: python3 repo_resolver.py --list")
        print("Or: python3 repo_resolver.py --default-root")
        sys.exit(1)
    
    if sys.argv[1] == "--list":
        print("📂 Available repos:\n")
        for repo in list_available_repos():
            print(f"  • {repo['name']}")
            print(f"    {repo['path']}")
            print(f"    (from {repo['location']})\n")
    
    elif sys.argv[1] == "--default-root":
        root = get_default_repos_root()
        print(f"Default repos root: {root}")
    
    else:
        path = resolve_repo(sys.argv[1])
        if path:
            print(f"✅ Resolved: {path}")
        else:
            print(f"❌ Not found: {sys.argv[1]}")
            print("\nSearching in:")
            for sp in get_search_paths():
                print(f"  • {sp}")
            sys.exit(1)
