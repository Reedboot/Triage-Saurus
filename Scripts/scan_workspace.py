#!/usr/bin/env python3
"""Optimized single-pass workspace scanner (stdout-only; no writes).

Purpose:
- Consolidates Knowledge/, Findings/, Intake/, and repo scanning into a single
  filesystem walk where possible.
- Reduces I/O overhead compared to multiple independent os.walk calls.

Maintains same interface as scan_workspace.py for drop-in replacement.

Usage:
  python3 Scripts/scan_workspace_v2.py
  python3 Scripts/scan_workspace_v2.py --intake Intake/Cloud
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from output_paths import (
    REPO_ROOT,
    OUTPUT_FINDINGS_DIR,
    OUTPUT_KNOWLEDGE_DIR,
)

# -----------------------------------------------------------------------------
# Knowledge refinement detection (from scan_knowledge_refinement.py)
# -----------------------------------------------------------------------------

HEADING_RE = re.compile(r"^##\s+(Unknowns|❓\s*Open\s+Questions)\s*$", re.IGNORECASE)
NEXT_SECTION_RE = re.compile(r"^#{1,2}\s+")


@dataclass(frozen=True)
class RefinementFinding:
    path: Path
    section: str
    line: int
    excerpt: list[str]


def _meaningful_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        out.append(line.rstrip("\n"))
    return out


def scan_knowledge_file(path: Path) -> list[RefinementFinding]:
    """Scan a single markdown file for refinement sections."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    findings: list[RefinementFinding] = []
    i = 0
    while i < len(text):
        m = HEADING_RE.match(text[i].strip())
        if not m:
            i += 1
            continue

        section = m.group(1)
        start = i + 1
        j = start
        while j < len(text) and not NEXT_SECTION_RE.match(text[j]):
            j += 1

        content = _meaningful_lines(text[start:j])
        if content:
            findings.append(RefinementFinding(
                path=path,
                section=section,
                line=i + 1,
                excerpt=content[:12],
            ))
        i = j

    return findings


# -----------------------------------------------------------------------------
# Repo candidate detection (from list_repo_candidates.py)
# -----------------------------------------------------------------------------

REPO_MARKERS = {
    "package.json", "requirements.txt", "pyproject.toml", "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "docker-compose.yml", "Dockerfile",
}

REPO_NAME_HINTS_INFRA = (
    "terraform", "iac", "infra", "infrastructure", "platform",
    "modules", "bicep", "cloudformation", "pulumi", "kubernetes", "helm",
)


def _looks_like_repo(dir_path: Path, *, max_depth: int = 2) -> bool:
    """Check if directory looks like a repo (early-exit on .git)."""
    if (dir_path / ".git").exists():
        return True

    try:
        for root, dirs, files in os.walk(dir_path):
            rel_depth = len(Path(root).relative_to(dir_path).parts)
            if rel_depth > max_depth:
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
    return "Infrastructure (likely IaC/platform)" if any(h in n for h in REPO_NAME_HINTS_INFRA) else "Application/Other"


# -----------------------------------------------------------------------------
# Single-pass scanner results
# -----------------------------------------------------------------------------

@dataclass
class ScanResults:
    """Accumulated results from single-pass scan."""
    knowledge_files: list[Path] = field(default_factory=list)
    refinement_findings: list[RefinementFinding] = field(default_factory=list)
    finding_files: list[Path] = field(default_factory=list)
    intake_files: dict[str, list[Path]] = field(default_factory=dict)  # path -> files


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def single_pass_scan(
    *,
    scan_knowledge: bool,
    scan_findings: bool,
    intake_paths: list[Path],
    findings_exts: set[str],
    intake_exts: set[str],
    include_hidden: bool,
) -> ScanResults:
    """
    Perform optimized scanning of Knowledge/, Findings/, and Intake/ folders.

    For paths under REPO_ROOT/Output/, uses single os.walk where possible.
    Intake paths outside Output/ are walked separately.
    """
    results = ScanResults()

    # Initialize intake_files dict
    for p in intake_paths:
        results.intake_files[str(p)] = []

    # Separate intake paths into those under REPO_ROOT and external
    repo_intake_paths = []
    external_intake_paths = []
    for p in intake_paths:
        try:
            p.relative_to(REPO_ROOT)
            repo_intake_paths.append(p)
        except ValueError:
            external_intake_paths.append(p)

    # Single pass over REPO_ROOT for Knowledge, Findings, and repo-local Intake
    if scan_knowledge or scan_findings or repo_intake_paths:
        _scan_repo_root(
            results,
            scan_knowledge=scan_knowledge,
            scan_findings=scan_findings,
            repo_intake_paths=repo_intake_paths,
            findings_exts=findings_exts,
            intake_exts=intake_exts,
            include_hidden=include_hidden,
        )

    # Walk external intake paths separately
    for intake_path in external_intake_paths:
        if intake_path.exists():
            files = _walk_for_exts(intake_path, intake_exts, include_hidden)
            results.intake_files[str(intake_path)] = files

    return results


def _scan_repo_root(
    results: ScanResults,
    *,
    scan_knowledge: bool,
    scan_findings: bool,
    repo_intake_paths: list[Path],
    findings_exts: set[str],
    intake_exts: set[str],
    include_hidden: bool,
) -> None:
    """Walk REPO_ROOT once, categorizing files as we go."""

    # Precompute paths to check
    knowledge_dir = OUTPUT_KNOWLEDGE_DIR if scan_knowledge and OUTPUT_KNOWLEDGE_DIR.exists() else None
    findings_dir = OUTPUT_FINDINGS_DIR if scan_findings and OUTPUT_FINDINGS_DIR.exists() else None

    # Build set of intake path prefixes for fast lookup
    intake_set = {p.resolve() for p in repo_intake_paths if p.exists()}

    # Directories to actually walk (avoid walking entire repo)
    walk_roots: set[Path] = set()
    if knowledge_dir:
        walk_roots.add(knowledge_dir)
    if findings_dir:
        walk_roots.add(findings_dir)
    for p in intake_set:
        walk_roots.add(p)

    for walk_root in walk_roots:
        for dirpath, dirnames, filenames in os.walk(walk_root):
            current = Path(dirpath)

            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not _is_hidden(d)]

            for fname in filenames:
                if not include_hidden and _is_hidden(fname):
                    continue

                fpath = current / fname
                ext = fpath.suffix.lower().lstrip(".")

                # Check knowledge
                if knowledge_dir and _is_under(fpath, knowledge_dir):
                    if ext == "md":
                        results.knowledge_files.append(fpath)
                        results.refinement_findings.extend(scan_knowledge_file(fpath))

                # Check findings
                elif findings_dir and _is_under(fpath, findings_dir):
                    if ext in findings_exts:
                        results.finding_files.append(fpath)

                # Check intake paths
                else:
                    for intake_path in intake_set:
                        if _is_under(fpath, intake_path) and ext in intake_exts:
                            results.intake_files[str(intake_path)].append(fpath)
                            break


def _is_under(path: Path, parent: Path) -> bool:
    """Check if path is under parent directory."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _walk_for_exts(root: Path, exts: set[str], include_hidden: bool) -> list[Path]:
    """Walk a directory for files with given extensions."""
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not _is_hidden(d)]
        for fname in filenames:
            if not include_hidden and _is_hidden(fname):
                continue
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower().lstrip(".") in exts:
                matches.append(fpath)
    return sorted(matches)


# -----------------------------------------------------------------------------
# Repo candidate scanning (separate walk - can't combine with above)
# -----------------------------------------------------------------------------

def scan_repo_candidates(repos_root: Path) -> list[tuple[str, str, Path]]:
    """List repo candidates under repos_root. Returns (name, classification, path)."""
    if not repos_root.is_dir():
        return []

    candidates: list[tuple[str, str, Path]] = []
    try:
        for entry in sorted(repos_root.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or _is_hidden(entry.name):
                continue
            if entry.resolve() == REPO_ROOT.resolve():
                continue
            if _looks_like_repo(entry):
                candidates.append((entry.name, classify_repo(entry.name), entry))
    except OSError:
        pass

    return candidates


def scan_sample_repo_candidates() -> list[tuple[str, str, Path]]:
    """List sample repo candidates shipped with workspace."""
    sample_root = REPO_ROOT / "Sample Findings" / "Repos"
    if not sample_root.is_dir():
        return []

    candidates: list[tuple[str, str, Path]] = []
    try:
        for entry in sorted(sample_root.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or _is_hidden(entry.name):
                continue
            if _looks_like_repo(entry):
                candidates.append((entry.name, classify_repo(entry.name), entry))
    except OSError:
        pass

    return candidates


# -----------------------------------------------------------------------------
# Draft triage queue (lazy import)
# -----------------------------------------------------------------------------

def scan_drafts(*, limit: int = 20) -> None:
    """Report draft vs validated findings."""
    print("== Draft triage queue ==")
    try:
        import triage_queue as tq
        tq.print_queue(limit=limit)
    except Exception as e:
        print(f"Unable to load triage queue helper: {e}")
    print()


# -----------------------------------------------------------------------------
# Output formatting
# -----------------------------------------------------------------------------

def _rel_path(path: Path, *, absolute: bool) -> str:
    if absolute:
        return str(path)
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def print_results(
    results: ScanResults,
    *,
    absolute: bool,
    show_knowledge: bool,
    show_findings: bool,
    show_intake: bool,
    show_drafts: bool,
) -> None:
    """Print scan results in standard format."""

    if show_knowledge:
        print("== Knowledge refinement ==")
        if not OUTPUT_KNOWLEDGE_DIR.exists():
            print(f"Knowledge directory not found: {OUTPUT_KNOWLEDGE_DIR}")
        else:
            print(f"Knowledge markdown files: {len(results.knowledge_files)}")
            for f in sorted(results.knowledge_files):
                print(f"- {_rel_path(f, absolute=absolute)}")

            print(f"\nOutstanding refinement sections: {len(results.refinement_findings)}")
            for item in results.refinement_findings:
                print(f"\n=== {_rel_path(item.path, absolute=absolute)}:{item.line} ({item.section}) ===")
                for line in item.excerpt:
                    print(line)
        print()

    if show_findings:
        print("== Findings scan ==")
        if not OUTPUT_FINDINGS_DIR.exists():
            print(f"Findings path does not exist: {OUTPUT_FINDINGS_DIR}")
        else:
            print(f"Findings scan path: {OUTPUT_FINDINGS_DIR}")
            print(f"Finding files: {len(results.finding_files)}")
            for f in sorted(results.finding_files):
                print(_rel_path(f, absolute=absolute))
        print()

        if show_drafts:
            scan_drafts()

    if show_intake:
        print("== Intake scan ==")
        if not results.intake_files:
            print("No intake paths provided.")
        else:
            for path_str, files in results.intake_files.items():
                path = Path(path_str)
                print(f"\nIntake scan path: {path}")
                if not path.exists():
                    print("Path does not exist")
                    continue
                print(f"Intake files: {len(files)}")
                for f in sorted(files):
                    print(_rel_path(f, absolute=absolute))
        print()


def print_repo_candidates(repos_root: Path | None, *, skip_repos: bool) -> None:
    """Print repo candidate listing."""
    if skip_repos:
        return

    print("== Repo candidates ==")
    default_root = REPO_ROOT.parent
    root = repos_root.resolve() if repos_root else default_root

    print(f"repos_root: {root}")
    print(f"workspace_repo_root: {REPO_ROOT}")
    print()

    if not root.is_dir():
        print("ERROR: repos_root is not a directory")
        print()
    else:
        candidates = scan_repo_candidates(root)
        print(f"candidates: {len(candidates)}")
        for name, classification, path in candidates:
            print(f"- {name} — {classification} — {path}")
        print()

    print("== Sample repo candidates ==")
    sample_root = REPO_ROOT / "Sample Findings" / "Repos"
    print(f"sample_repos_root: {sample_root}")

    if not sample_root.exists():
        print("Sample repos folder not found.")
    elif not sample_root.is_dir():
        print("ERROR: sample_repos_root is not a directory")
    else:
        candidates = scan_sample_repo_candidates()
        print(f"candidates: {len(candidates)}")
        for name, classification, path in candidates:
            print(f"- {name} — {classification} — {_rel_path(path, absolute=False)}")
    print()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Optimized single-pass scan of Knowledge/, Findings/, and Intake/."
    )
    parser.add_argument("--skip-repos", action="store_true", help="Skip repo-candidate listing.")
    parser.add_argument("--repos-root", default=None, help="Root folder containing repositories.")
    parser.add_argument("--skip-knowledge", action="store_true", help="Skip Knowledge/ refinement scan.")
    parser.add_argument("--skip-findings", action="store_true", help="Skip Findings/ scan.")
    parser.add_argument("--skip-intake", action="store_true", help="Skip Intake/ scan.")
    parser.add_argument("--skip-drafts", action="store_true", help="Skip draft-triage queue summary.")
    parser.add_argument("--findings-path", default="Findings", help="Folder to scan for findings.")
    parser.add_argument("--intake", action="append", default=None, help="Intake folder/file to scan (repeatable).")
    parser.add_argument("--findings-ext", action="append", default=None, help="Findings extension (repeatable).")
    parser.add_argument("--intake-ext", action="append", default=None, help="Intake extension (repeatable).")
    parser.add_argument("--absolute", action="store_true", help="Print absolute paths.")
    parser.add_argument("--include-hidden", action="store_true", help="Include hidden files/directories.")

    args = parser.parse_args()

    findings_exts = {e.lower().lstrip(".") for e in (args.findings_ext or ["md"])}
    intake_exts = {e.lower().lstrip(".") for e in (args.intake_ext or ["txt", "csv", "md"])}

    # Default intake paths
    intake_paths_raw = args.intake or [
        "Intake/Cloud",
        "Intake/Code",
        "Intake/Sample/Cloud",
        "Intake/Sample/Code",
        "Sample Findings/Cloud",
        "Sample Findings/Code",
    ]

    # Resolve intake paths
    intake_paths: list[Path] = []
    for raw in intake_paths_raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = REPO_ROOT / p
        intake_paths.append(p.resolve())

    # Perform single-pass scan
    results = single_pass_scan(
        scan_knowledge=not args.skip_knowledge,
        scan_findings=not args.skip_findings,
        intake_paths=intake_paths if not args.skip_intake else [],
        findings_exts=findings_exts,
        intake_exts=intake_exts,
        include_hidden=args.include_hidden,
    )

    # Print results
    print_results(
        results,
        absolute=args.absolute,
        show_knowledge=not args.skip_knowledge,
        show_findings=not args.skip_findings,
        show_intake=not args.skip_intake,
        show_drafts=not args.skip_drafts,
    )

    # Print repo candidates (separate walk - can't optimize further)
    repos_root = Path(args.repos_root).expanduser().resolve() if args.repos_root else None
    print_repo_candidates(repos_root, skip_repos=args.skip_repos)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
