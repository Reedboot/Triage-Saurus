#!/usr/bin/env python3
"""Shared utility functions for Triage-Saurus scripts.

This module consolidates functions that were duplicated across multiple
scripts. Import from here instead of defining local copies.

Functions
---------
Title helpers:
    _normalise_title, _dedupe_key

Timestamp helpers:
    now_uk, _now_stamp

Finding iteration:
    iter_findings

Markdown section helpers:
    _update_last_updated, _find_heading, _slice_section_body,
    _extract_first_match, _normalise_status, _normalise_compounds_with

Filesystem helpers:
    _is_hidden, iter_files

Severity scoring:
    _severity_score

Draft detection:
    is_draft_finding
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Title helpers
# ---------------------------------------------------------------------------

def _normalise_title(line: str) -> str:
    """Normalise a title line: strip BOM, leading '#', and surrounding whitespace."""
    return line.strip().lstrip("\ufeff").lstrip("# ").strip()


def _dedupe_key(title: str) -> str:
    """Coarse dedupe key for bulk imports: normalise, lowercase, trim punctuation."""
    s = _normalise_title(title).lower()
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    s = re.sub(r"(/etc/(?:shadow|gshadow|passwd|group))\-(?=\s)", r"\1", s)
    return s


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def now_uk() -> str:
    """Return the current datetime as a UK-format string (dd/mm/yyyy HH:MM)."""
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


def _now_stamp() -> str:
    """Return the current datetime in dd/mm/yyyy HH:MM format (repo convention)."""
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# Finding iteration
# ---------------------------------------------------------------------------

def iter_findings(root: Path) -> list[Path]:
    """Return a sorted list of .md finding files under *root*.

    If *root* is itself a .md file it is returned directly.
    Returns an empty list when *root* does not exist.
    """
    if root.is_file() and root.suffix.lower() == ".md":
        return [root]
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


# ---------------------------------------------------------------------------
# Markdown section helpers
# ---------------------------------------------------------------------------

def _update_last_updated(lines: list[str]) -> list[str]:
    """Replace the '🗓️ **Last updated:**' line with the current timestamp.

    Returns *lines* unchanged when no matching line is found.
    """
    stamp = _now_stamp()
    out: list[str] = []
    updated = False
    for line in lines:
        if re.match(r"^\-\s*🗓️\s*\*\*Last updated:\*\*", line):
            out.append(f"- 🗓️ **Last updated:** {stamp}")
            updated = True
        else:
            out.append(line)
    return out if updated else lines


def _find_heading(lines: list[str], heading: str) -> int | None:
    """Return the index of the line that exactly matches *heading*, or None."""
    for i, line in enumerate(lines):
        if line.strip() == heading:
            return i
    return None


def _slice_section_body(lines: list[str], heading_idx: int) -> tuple[int, int]:
    """Return *(body_start, body_end_exclusive)* for the section starting at *heading_idx*.

    The section ends at the next heading of the same or higher level (##/###/#)
    or at EOF.
    """
    start = heading_idx + 1
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^#{1,3}\s+", lines[j]):
            end = j
            break
    return start, end


def _extract_first_match(lines: list[str], pattern: re.Pattern) -> str:  # type: ignore[type-arg]
    """Return the first captured group from the first line matched by *pattern*, or ''."""
    for line in lines:
        m = pattern.search(line)
        if m:
            return m.group(1).strip()
    return ""


def _normalise_status(s: str) -> str:
    """Normalise an applicability status string to a canonical lowercase token."""
    s = (s or "").strip().lower()
    if s in {"yes", "y"}:
        return "yes"
    if s in {"no", "n", "not applicable", "n/a"}:
        return "no"
    if "don't know" in s or "don\u2019t know" in s:
        return "dont_know"
    return s


def _normalise_compounds_with(s: str) -> str:
    """Return '' for empty/None/sentinel values, otherwise the trimmed string."""
    s = (s or "").strip()
    if not s:
        return ""
    if s.lower() in {"none", "none identified", "n/a"}:
        return ""
    return s


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _is_hidden(path: "Path | str", root: "Path | None" = None) -> bool:
    """Check whether a path is hidden (any component starts with '.').

    Accepts either a filename string or a :class:`~pathlib.Path`.  When *root*
    is provided the check is performed on the path relative to *root*.
    """
    if isinstance(path, str):
        return path.startswith(".")
    if root is not None:
        try:
            rel = path.relative_to(root)
            return any(part.startswith(".") for part in rel.parts)
        except ValueError:
            return False
    return any(part.startswith(".") for part in path.parts)


def iter_files(
    root: Path,
    *,
    exts: "set[str] | None" = None,
    include_hidden: bool = False,
    max_depth: "int | None" = None,
    skip_dirs: "set[str] | None" = None,
) -> list[Path]:
    """Walk *root* recursively and return matching files sorted by path.

    Parameters
    ----------
    root:
        Directory to walk (resolved to absolute path internally).
    exts:
        Set of lowercase extensions without the leading dot to include
        (e.g. ``{"md", "txt"}``).  *None* means include all extensions.
    include_hidden:
        When *False* (the default) hidden files and directories (names
        starting with ``'.'``) are skipped.
    max_depth:
        Maximum recursion depth relative to *root*.  *None* means unlimited.
    skip_dirs:
        Set of directory *names* (not full paths) to skip entirely
        (e.g. ``{".git"}``).
    """
    matches: list[Path] = []
    root = root.resolve()
    _skip = skip_dirs or set()

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)

        if max_depth is not None and depth >= max_depth:
            dirnames[:] = []
        else:
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            dirnames[:] = [d for d in dirnames if d not in _skip]

        for fname in filenames:
            if not include_hidden and fname.startswith("."):
                continue
            fpath = current / fname
            if exts is not None and fpath.suffix.lower().lstrip(".") not in exts:
                continue
            matches.append(fpath)

    return sorted(matches)


# ---------------------------------------------------------------------------
# Severity scoring
# ---------------------------------------------------------------------------

def _severity_score(severity: "str | None") -> int:
    """Map an opengrep/scan severity string to an integer score (0-10 scale).

    ERROR → 8, WARNING → 5, INFO → 2, unknown/None → 4.
    """
    if not severity:
        return 4
    value = severity.upper()
    if value == "ERROR":
        return 8
    if value == "WARNING":
        return 5
    if value == "INFO":
        return 2
    return 4


# ---------------------------------------------------------------------------
# Draft finding detection
# ---------------------------------------------------------------------------

_DRAFT_INDICATORS = [
    "draft finding generated from a title-only input",
    "this is a draft finding",
    "validate the affected resources/scope",
    "title-only input; needs validation",
]


def is_draft_finding(text: str) -> bool:
    """Return *True* when *text* looks like an unvalidated draft finding.

    Prefers the explicit ``Validation Status`` metadata field; falls back to
    phrase matching against known boilerplate strings.
    """
    if "Validation Status:** \u26a0\ufe0f Draft - Needs Triage" in text:
        return True
    if "Validation Status:** \u2705 Validated" in text:
        return False
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in _DRAFT_INDICATORS)


# ---------------------------------------------------------------------------
# Generate-script helpers (shared between generate_findings_from_titles.py
# and generate_code_findings_from_titles.py)
# ---------------------------------------------------------------------------

def titlecase_filename(title: str) -> str:
    """Convert a title string to a safe TitleCase filename stem."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    parts = [p for p in cleaned.split("_") if p]
    return "_".join([p[:1].upper() + p[1:] for p in parts]) or "Finding"


def _unique_out_path(out_dir: "Path", base: str) -> "Path":
    """Return a non-colliding .md output path under *out_dir*."""
    out = out_dir / f"{base}.md"
    if not out.exists():
        return out
    i = 2
    while True:
        candidate = out_dir / f"{base}_{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


def severity(score: int) -> str:
    """Convert a numeric risk score to an emoji severity label."""
    if score >= 8:
        return "\U0001f534 Critical"
    if score >= 6:
        return "\U0001f7e0 High"
    if score >= 4:
        return "\U0001f7e1 Medium"
    return "\U0001f7e2 Low"


def _normalize_optional_bool(value: object) -> "bool | None":
    """Coerce a value to bool or None.  Handles 0/1, 'true'/'false', 'yes'/'no' etc."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "t"}:
        return True
    if lowered in {"0", "false", "no", "n", "f"}:
        return False
    return None
