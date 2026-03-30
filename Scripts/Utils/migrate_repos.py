#!/usr/bin/env python3
"""
migrate_repos.py — Move repos from Windows filesystem to native Linux filesystem for performance.

Usage:
    python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --list
    python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --migrate account-viewing-permissions
    python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --migrate-all
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "Settings" / "paths.json"


def load_config():
    """Load paths.json config."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"repo_search_paths": [], "default_repo_root": "~/repos"}


def save_config(config):
    """Save paths.json config."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def list_repos(source_dir: Path):
    """List all repos in source directory."""
    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}")
        return []
    
    repos = [d for d in source_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    return sorted(repos, key=lambda x: x.name)


def migrate_repo(source: Path, dest: Path, repo_name: str, dry_run: bool = False):
    """Copy repo from source to destination."""
    src_path = source / repo_name
    dst_path = dest / repo_name
    
    if not src_path.exists():
        print(f"❌ Source repo not found: {src_path}")
        return False
    
    if dst_path.exists():
        print(f"⚠️  Destination already exists: {dst_path}")
        return False
    
    size = subprocess.check_output(["du", "-sh", str(src_path)]).decode().split()[0]
    
    if dry_run:
        print(f"[DRY RUN] Would copy {repo_name} ({size}) from {src_path} to {dst_path}")
        return True
    
    print(f"📦 Copying {repo_name} ({size})...")
    dest.mkdir(parents=True, exist_ok=True)
    
    # Use rsync for efficient copy with progress
    cmd = ["rsync", "-ah", "--info=progress2", str(src_path) + "/", str(dst_path)]
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print(f"✅ Migrated {repo_name} to {dst_path}")
        return True
    else:
        print(f"❌ Failed to migrate {repo_name}")
        return False


def update_search_paths(new_path: Path):
    """Add new repo root to search paths."""
    config = load_config()
    search_paths = config.get("repo_search_paths", [])
    
    new_path_str = str(new_path)
    if new_path_str not in search_paths:
        search_paths.insert(0, new_path_str)
        config["repo_search_paths"] = search_paths
        config["default_repo_root"] = new_path_str
        save_config(config)
        print(f"✅ Updated Settings/paths.json - {new_path_str} is now the default repo root")


def main():
    parser = argparse.ArgumentParser(description="Migrate repos to native Linux filesystem")
    parser.add_argument("--from", dest="source", required=True, help="Source directory (e.g., /mnt/c/Repos)")
    parser.add_argument("--to", dest="destination", required=True, help="Destination directory (e.g., ~/repos)")
    parser.add_argument("--list", action="store_true", help="List repos in source directory")
    parser.add_argument("--migrate", metavar="REPO", help="Migrate specific repo")
    parser.add_argument("--migrate-all", action="store_true", help="Migrate all repos")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    
    args = parser.parse_args()
    
    source = Path(args.source).expanduser().resolve()
    dest = Path(args.destination).expanduser().resolve()
    
    if args.list:
        repos = list_repos(source)
        print(f"\n📂 Repos in {source}:\n")
        for repo in repos:
            size = subprocess.check_output(["du", "-sh", str(repo)]).decode().split()[0]
            print(f"  • {repo.name} ({size})")
        print(f"\nTotal: {len(repos)} repos")
        return
    
    if args.migrate:
        success = migrate_repo(source, dest, args.migrate, args.dry_run)
        if success and not args.dry_run:
            update_search_paths(dest)
    
    elif args.migrate_all:
        repos = list_repos(source)
        print(f"\n🚀 Migrating {len(repos)} repos from {source} to {dest}\n")
        
        success_count = 0
        for repo in repos:
            if migrate_repo(source, dest, repo.name, args.dry_run):
                success_count += 1
        
        print(f"\n✅ Successfully migrated {success_count}/{len(repos)} repos")
        if success_count > 0 and not args.dry_run:
            update_search_paths(dest)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
