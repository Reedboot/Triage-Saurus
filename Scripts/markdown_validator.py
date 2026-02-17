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

_NODE_SQUARE_LABEL_RE = re.compile(r"(\b[A-Za-z_][A-Za-z0-9_]*\s*)\[(.*?)\]")
_NODE_QUOTED_LABEL_RE = re.compile(r"(\b[A-Za-z_][A-Za-z0-9_]*\s*)\\[\"(.*?)\"\\]")
_SUBGRAPH_QUOTED_RE = re.compile(r"(\bsubgraph\b[^\"]*\\[\"(.*?)\"\\])", re.IGNORECASE)


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

        # Renderer-compat fixes: replace escaped newlines / HTML breaks in labels.
        # Some Mermaid renderers reject these; prefer a single-line label for broad compatibility.
        if any("\\n" in b for b in block) or any(re.search(r"<\s*br\s*/?\s*>", b, re.IGNORECASE) for b in block):
            for j, b in enumerate(block):
                if "\\n" in b:
                    problems.append(
                        Problem(
                            Path("."),
                            "WARN",
                            r"Mermaid contains a literal '\n' sequence; renderer support varies. Prefer single-line labels for compatibility.",
                            start_line_no + j,
                        )
                    )
                if re.search(r"<\s*br\s*/?\s*>", b, re.IGNORECASE):
                    problems.append(
                        Problem(
                            Path("."),
                            "WARN",
                            "Mermaid contains an HTML <br> tag; renderer support varies. Prefer single-line labels for compatibility.",
                            start_line_no + j,
                        )
                    )
            if fix:
                new_block: list[str] = []
                for b in block:
                    bb = b.replace("\\n", " - ")
                    bb2 = re.sub(r"<\s*br\s*/?\s*>", " - ", bb, flags=re.IGNORECASE)
                    if bb2 != b:
                        changed = True
                    new_block.append(bb2)
                block = new_block

        # Renderer-compat fixes: some renderers reject non-ASCII (e.g., emojis) in Mermaid.
        # Prefer ASCII-only labels for broadest compatibility.
        if any(any(ord(ch) > 127 for ch in b) for b in block):
            for j, b in enumerate(block):
                if any(ord(ch) > 127 for ch in b):
                    problems.append(
                        Problem(
                            Path("."),
                            "WARN",
                            "Mermaid contains non-ASCII characters (e.g., emoji); renderer support varies. Prefer ASCII-only labels for compatibility.",
                            start_line_no + j,
                        )
                    )
            if fix:
                new_block2: list[str] = []
                for b in block:
                    bb = "".join(ch for ch in b if ord(ch) <= 127)
                    if bb != b:
                        changed = True
                    new_block2.append(bb)
                block = new_block2

        # Renderer-compat fixes: some parsers reject parentheses in labels inside [] / [""].
        # Heuristic: warn and (optionally) strip parentheses from label text only.
        if any("(" in b or ")" in b for b in block):
            for j, b in enumerate(block):
                if "(" in b or ")" in b:
                    problems.append(
                        Problem(
                            Path("."),
                            "WARN",
                            "Mermaid contains parentheses; some renderers reject parentheses inside labels. Prefer removing parentheses for compatibility.",
                            start_line_no + j,
                        )
                    )
            if fix:
                def _strip_parens(s: str) -> str:
                    return s.replace("(", "").replace(")", "")

                def _fix_square(m: re.Match[str]) -> str:
                    return f"{m.group(1)}[{_strip_parens(m.group(2))}]"

                def _fix_quoted(m: re.Match[str]) -> str:
                    return f'{m.group(1)}["{_strip_parens(m.group(2))}"]'

                new_block3: list[str] = []
                for b in block:
                    bb = _NODE_QUOTED_LABEL_RE.sub(_fix_quoted, b)
                    bb = _NODE_SQUARE_LABEL_RE.sub(_fix_square, bb)
                    # Also handle subgraph titles expressed as ["..."] that don't match node-id patterns.
                    if bb != b:
                        changed = True
                    new_block3.append(bb)
                block = new_block3

        # Renderer-compat fixes: some parsers reject curly braces in labels (conflicts with Mermaid shape syntax).
        if any("{" in b or "}" in b for b in block):
            for j, b in enumerate(block):
                if "{" in b or "}" in b:
                    problems.append(
                        Problem(
                            Path("."),
                            "WARN",
                            "Mermaid contains '{' or '}' in labels; some renderers reject this. Prefer using ':id' style instead of '{id}'.",
                            start_line_no + j,
                        )
                    )
            if fix:
                def _strip_braces(s: str) -> str:
                    return s.replace("{", "").replace("}", "")

                def _fix_square2(m: re.Match[str]) -> str:
                    return f"{m.group(1)}[{_strip_braces(m.group(2))}]"

                def _fix_quoted2(m: re.Match[str]) -> str:
                    return f'{m.group(1)}["{_strip_braces(m.group(2))}"]'

                new_block4: list[str] = []
                for b in block:
                    bb = _NODE_QUOTED_LABEL_RE.sub(_fix_quoted2, b)
                    bb = _NODE_SQUARE_LABEL_RE.sub(_fix_square2, bb)
                    if bb != b:
                        changed = True
                    new_block4.append(bb)
                block = new_block4

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
