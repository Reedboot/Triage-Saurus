#!/usr/bin/env python3
"""Quick, dependency-light repo scan helper for this workspace.

Usage:
  python3 Scripts/scan_repo_quick.py /abs/path/to/repo

Output: stdout only (intended for interactive triage), no file writes.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


KEY_FILE_PATTERNS = [
    "*.tf",
    "*.tfvars",
    "*.md",
    "*.yml",
    "*.yaml",
    "*.json",
    "Dockerfile",
    "docker-compose.yml",
    "package.json",
    "requirements.txt",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "*.csproj",
]

TF_HEAD_RE = re.compile(r"^(terraform|provider|module)\b")
SECRETS_RE = re.compile(
    r"(password|passwd|secret|token|apikey|api_key|client_secret|connectionstring|connection_string)",
    re.IGNORECASE,
)

# Language/framework detection rules
# Format: (name, marker_files, marker_patterns)
# marker_files: exact filenames or wildcards like *.ext
# marker_patterns: used for secondary detection (not currently used)
LANGUAGE_RULES = [
    ("Terraform", ["*.tf", "*.tfvars"], []),
    ("Go", ["go.mod", "go.sum"], []),
    ("Node.js", ["package.json", "package-lock.json", "yarn.lock"], []),
    ("Python", ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"], []),
    (".NET", ["*.csproj", "*.fsproj", "*.vbproj", "*.sln"], []),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"], []),
    ("Ruby", ["Gemfile", "Gemfile.lock"], []),
    ("PHP", ["composer.json", "composer.lock"], []),
    ("Rust", ["Cargo.toml", "Cargo.lock"], []),
]


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def iter_files(repo: Path, max_depth: int) -> list[Path]:
    files: list[Path] = []
    repo = repo.resolve()
    for p in repo.rglob("*"):
        try:
            rel = p.relative_to(repo)
        except ValueError:
            continue
        if ".git" in rel.parts:
            continue
        if len(rel.parts) > max_depth:
            continue
        if p.is_file():
            files.append(p)
    return files


def detect_languages(file_list: list[Path], repo: Path) -> list[tuple[str, str]]:
    """Returns list of (language, evidence) tuples based on collected files."""
    detected: list[tuple[str, str]] = []
    
    # Check each language rule
    for lang, markers, patterns in LANGUAGE_RULES:
        evidence = None
        # Check for marker files in already-collected list
        for marker in markers:
            if "*" in marker:
                # Pattern match (e.g., *.tf, *.csproj)
                ext = marker.replace("*", "")
                for p in file_list:
                    if str(p).endswith(ext):
                        try:
                            rel = p.relative_to(repo)
                            evidence = rel.as_posix()
                            break
                        except ValueError:
                            continue
            else:
                # Exact file name match
                for p in file_list:
                    if p.name == marker:
                        try:
                            rel = p.relative_to(repo)
                            evidence = rel.as_posix()
                            break
                        except ValueError:
                            continue
            
            if evidence:
                break
        
        if evidence:
            detected.append((lang, evidence))
    
    return detected


def main() -> int:
    if len(sys.argv) != 2:
        eprint(f"Usage: {sys.argv[0]} /abs/path/to/repo")
        return 2

    repo = Path(sys.argv[1])
    if not repo.is_dir():
        eprint(f"ERROR: repo path not found: {repo}")
        return 2

    repo = repo.resolve()

    print("== Repo ==")
    print(str(repo))
    print()
    
    # Collect files once
    all_files = iter_files(repo, max_depth=10)

    print("== Languages/frameworks detected ==")
    langs = detect_languages(all_files, repo)
    if langs:
        for lang, evidence in langs:
            print(f"{lang} — evidence: {evidence}")
    else:
        print("(none detected)")
    print()

    print("== Top-level ==")
    try:
        for entry in sorted(repo.iterdir(), key=lambda p: p.name.lower()):
            st = entry.stat()
            kind = "d" if entry.is_dir() else "-"
            print(f"{kind} {st.st_size:>10} {entry.name}")
    except OSError as ex:
        eprint(f"ERROR: cannot list top-level: {ex}")
        return 1

    print()
    print("== Key files (top 80) ==")
    key_hits: list[str] = []
    for pat in KEY_FILE_PATTERNS:
        for p in all_files:
            if pat.startswith("*"):
                # Pattern like *.tf
                if str(p).endswith(pat[1:]):
                    rel = p.relative_to(repo)
                    if len(rel.parts) <= 4:
                        key_hits.append(f"./{rel.as_posix()}")
            else:
                # Exact filename like Dockerfile
                if p.name == pat:
                    rel = p.relative_to(repo)
                    if len(rel.parts) <= 4:
                        key_hits.append(f"./{rel.as_posix()}")
    for line in sorted(set(key_hits))[:80]:
        print(line)

    print()
    print("== Terraform module/provider usage (first 120 matches) ==")
    tf_files = [p for p in all_files if p.suffix == ".tf"]
    tf_matches = 0
    for tf in sorted(tf_files):
        rel = tf.relative_to(repo).as_posix()
        try:
            with tf.open("r", encoding="utf-8", errors="replace") as f:
                for i, raw in enumerate(f, start=1):
                    if TF_HEAD_RE.search(raw):
                        print(f"./{rel}:{i}:{raw.rstrip()}" )
                        tf_matches += 1
                        if tf_matches >= 120:
                            break
        except OSError:
            continue
        if tf_matches >= 120:
            break

    print()
    print("== Potential secrets (first 120 matches) ==")
    scan_exts = {".tf", ".yml", ".yaml", ".json", ".ps1", ".sh", ".go", ".py", ".js", ".ts", ".md"}
    files = [p for p in all_files if p.suffix.lower() in scan_exts or p.name in {"Dockerfile", "docker-compose.yml"}]
    sec_matches = 0
    for fp in sorted(files):
        rel = fp.relative_to(repo).as_posix()
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for i, raw in enumerate(f, start=1):
                    if SECRETS_RE.search(raw):
                        print(f"./{rel}:{i}:{raw.rstrip()}" )
                        sec_matches += 1
                        if sec_matches >= 120:
                            break
        except OSError:
            continue
        if sec_matches >= 120:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
