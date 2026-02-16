#!/usr/bin/env python3
"""Markdown + Mermaid validation helpers (stdlib-only).

Goal
- Catch malformed Mermaid code blocks early.
- Provide safe, minimal auto-fixes for common generation issues (missing closing fence,
  missing diagram type line, tabs).

This is intentionally heuristic; it does not execute Mermaid.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MERMAID_FENCE_RE = re.compile(r"^```\s*mermaid\s*$", re.IGNORECASE)
FENCE_RE = re.compile(r"^```\s*$")

# Mermaid diagram directives (first non-empty line inside the block).
MERMAID_DIRECTIVE_RE = re.compile(
    r"^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|stateDiagram-v2|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph)\b",
    re.IGNORECASE,
)


@dataclass
class Problem:
    path: Path
    level: str  # ERROR|WARN
    message: str
    line: int | None = None


def validate_and_fix_mermaid_blocks(text: str, *, fix: bool) -> tuple[list[Problem], str, bool]:
    """Validate Mermaid fences; optionally auto-fix safe issues."""

    lines = text.splitlines()
    out: list[str] = []
    problems: list[Problem] = []
    changed = False

    i = 0
    while i < len(lines):
        line = lines[i]

        if not MERMAID_FENCE_RE.match(line.strip()):
            out.append(line)
            i += 1
            continue

        # Start of mermaid block.
        start_line_no = i + 1
        out.append("```mermaid")
        if line != "```mermaid":
            changed = True

        block: list[str] = []
        i += 1

        while i < len(lines) and not FENCE_RE.match(lines[i].strip()):
            block.append(lines[i])
            i += 1

        has_closing = i < len(lines) and FENCE_RE.match(lines[i].strip())

        # Normalise tabs (safe fix).
        if any("\t" in b for b in block):
            problems.append(Problem(Path("."), "WARN", "Mermaid block contains tabs; normalised to spaces", start_line_no))
            if fix:
                block = [b.replace("\t", "  ") for b in block]
                changed = True

        # Check for forbidden 'style fill' usage (breaks dark themes).
        for j, b in enumerate(block):
            if re.search(r"\bstyle\s+\S+\s+fill:", b, re.IGNORECASE):
                problems.append(
                    Problem(
                        Path("."),
                        "ERROR",
                        "Mermaid 'style fill' breaks dark themes; use stroke-width/stroke-dasharray instead (see Settings/Styling.md)",
                        start_line_no + j,
                    )
                )

        # Determine first non-empty line.
        first_non_empty_i = next((j for j, b in enumerate(block) if b.strip()), None)
        directive_line = block[first_non_empty_i].strip() if first_non_empty_i is not None else ""

        if not directive_line:
            problems.append(Problem(Path("."), "ERROR", "Empty Mermaid block", start_line_no))
            if fix:
                block = ["flowchart TB", "  A[TODO]"]
                changed = True
        elif not MERMAID_DIRECTIVE_RE.match(directive_line):
            problems.append(
                Problem(
                    Path("."),
                    "ERROR",
                    "Mermaid block missing/invalid diagram directive (e.g., 'flowchart TB')",
                    start_line_no,
                )
            )
            if fix:
                # Insert a safe default directive at the top.
                block = ["flowchart TB", *block]
                changed = True

        out.extend(block)

        if has_closing:
            out.append("```")
            i += 1
        else:
            problems.append(Problem(Path("."), "ERROR", "Mermaid code fence not closed", start_line_no))
            if fix:
                out.append("```")
                changed = True
            # No increment; we already hit EOF.

    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return problems, new_text, changed


def validate_markdown_file(path: Path, *, fix: bool) -> list[Problem]:
    text = path.read_text(encoding="utf-8", errors="replace")
    probs, new_text, changed = validate_and_fix_mermaid_blocks(text, fix=fix)

    # Fill in proper file path in problems.
    for p in probs:
        p.path = path

    if fix and changed and new_text != text:
        path.write_text(new_text, encoding="utf-8")

    return probs
