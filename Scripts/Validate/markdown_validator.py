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
_NODE_QUOTED_LABEL_RE = re.compile(r'(\b[A-Za-z_][A-Za-z0-9_]*\s*)\["(.*?)"\]')
_STYLE_PROPERTY_REPLACEMENTS = (
    ("stroke_width", "stroke-width"),
    ("stroke_dasharray", "stroke-dasharray"),
    ("stroke_opacity", "stroke-opacity"),
    ("fill_opacity", "fill-opacity"),
    ("font_size", "font-size"),
    ("font_weight", "font-weight"),
    ("text_anchor", "text-anchor"),
    ("line_height", "line-height"),
)

# Mermaid syntax validation patterns
_EMOJI_RE = re.compile(r'[\U0001F300-\U0001F9FF\U0001F000-\U0001F02F\U0001F0A0-\U0001F0FF]')
_SUBGRAPH_WITH_CLASS_RE = re.compile(r'^\s*subgraph\s+(\S+)\[([^\]]*)\]\s*:::\s*(\S+)', re.MULTILINE)
_SUBGRAPH_RE = re.compile(r'^\s*subgraph\s+(\S+)\[([^\]]*)\]', re.MULTILINE)


@dataclass
class Problem:
    path: Path
    level: str  # ERROR|WARN
    message: str
    line: int | None = None


def _check_emoji_with_class_suffix(block: list[str], start_line_no: int) -> list[Problem]:
    """Check for emojis in subgraph labels combined with class suffixes (causes Mermaid syntax errors).
    
    Pattern that fails: subgraph n23["🔒 dev-vm"]:::icon-azurerm-virtual-machine
    The emoji in the label combined with the class suffix causes Mermaid parser to fail.
    """
    problems: list[Problem] = []
    
    for j, line in enumerate(block):
        # Check for subgraph with class suffix
        if ':::' in line and 'subgraph' in line:
            # Extract the label part (between quotes or brackets)
            label_match = re.search(r'\["?([^\]"]*)"?\]', line)
            if label_match:
                label = label_match.group(1)
                # Check if label contains emoji
                if _EMOJI_RE.search(label):
                    problems.append(Problem(
                        Path("."), "ERROR",
                        f"Mermaid subgraph has emoji in label AND class suffix (causes parser error): '{label[:50]}...' with ':::' class. Remove emoji from label when using class suffix.",
                        start_line_no + j,
                    ))
    
    return problems


def _check_node_with_class_suffix(block: list[str], start_line_no: int) -> list[Problem]:
    """Check for invalid combinations of node definitions and class suffixes.
    
    Some patterns can cause issues when emojis or special characters are in labels with class suffixes.
    """
    problems: list[Problem] = []
    
    for j, line in enumerate(block):
        # Check for node definition with class suffix (node_id["label"]:::class)
        if ':::' in line and '[' in line and ']' in line and 'subgraph' not in line:
            # Extract what's in brackets
            bracket_match = re.search(r'\["?([^\]"]*)"?\]', line)
            if bracket_match:
                label = bracket_match.group(1)
                # Check if label has emoji and class suffix on same line
                if _EMOJI_RE.search(label) and ':::' in line:
                    problems.append(Problem(
                        Path("."), "ERROR",
                        f"Mermaid node has emoji in label AND class suffix: '{label[:50]}...'. Remove emoji from node label when using class suffix.",
                        start_line_no + j,
                    ))
    
    return problems


def _check_unbalanced_brackets(block: list[str], start_line_no: int) -> list[Problem]:
    """Check for unbalanced brackets/braces in Mermaid lines."""
    problems: list[Problem] = []
    
    for j, line in enumerate(block):
        # Skip comments
        if line.lstrip().startswith('%'):
            continue
        
        # Count brackets (excluding those in comments)
        code_part = line.split('%')[0]  # Remove everything after %
        
        square_open = code_part.count('[')
        square_close = code_part.count(']')
        paren_open = code_part.count('(')
        paren_close = code_part.count(')')
        
        # Only flag if clearly unbalanced (allow some flex for complex expressions)
        if square_open != square_close:
            problems.append(Problem(
                Path("."), "ERROR",
                f"Unbalanced square brackets: {square_open} '[' vs {square_close} ']'",
                start_line_no + j,
            ))
        
        if paren_open != paren_close:
            problems.append(Problem(
                Path("."), "WARN",
                f"Potentially unbalanced parentheses: {paren_open} '(' vs {paren_close} ')'",
                start_line_no + j,
            ))
    
    return problems


def _check_invalid_node_ids(block: list[str], start_line_no: int) -> list[Problem]:
    """Check for invalid node ID patterns that could cause Mermaid parser errors."""
    problems: list[Problem] = []
    
    # Mermaid node IDs must be alphanumeric, underscore, or hyphen (with some restrictions)
    # Pattern: node_id[label] or node_id["label"] at start of line (with optional indent)
    invalid_node_id_re = re.compile(r'^\s*([^\s\[]+)\s*\[')
    
    for j, line in enumerate(block):
        # Skip lines that are clearly not node definitions
        if any(line.lstrip().startswith(kw) for kw in ('subgraph', 'end', 'style', 'linkStyle', 'classDef', '%')):
            continue
        
        match = invalid_node_id_re.match(line)
        if match:
            node_id = match.group(1)
            # Check for problematic characters in node IDs (though many are now allowed in newer Mermaid)
            if ':::' in node_id or '"' in node_id or "'" in node_id:
                problems.append(Problem(
                    Path("."), "ERROR",
                    f"Invalid node ID '{node_id}'; contains characters that conflict with Mermaid syntax",
                    start_line_no + j,
                ))
    
    return problems



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

        if any(any(bad in b for bad, _ in _STYLE_PROPERTY_REPLACEMENTS) for b in block):
            if fix:
                new_block0: list[str] = []
                for b in block:
                    bb = b
                    for bad, good in _STYLE_PROPERTY_REPLACEMENTS:
                        bb = bb.replace(bad, good)
                    if bb != b:
                        changed = True
                    new_block0.append(bb)
                block = new_block0

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

        # Renderer-compat fixes: some renderers reject non-ASCII (e.g., emojis) in Mermaid
        # node labels (inside [...] or ["..."]).  Subgraph titles are fine in modern renderers,
        # so only strip from node label content, not from subgraph header lines.
        _NODE_LABEL_RE = re.compile(r'(\[\"?)(.*?)(\"?\])')

        def _strip_non_ascii_labels(line: str) -> str:
            """Strip non-ASCII only from the label portion of node/edge definitions."""
            def _clean(m: re.Match) -> str:
                cleaned = "".join(ch for ch in m.group(2) if ord(ch) <= 127)
                return f"{m.group(1)}{cleaned}{m.group(3)}"
            return _NODE_LABEL_RE.sub(_clean, line)

        if any(any(ord(ch) > 127 for ch in b) for b in block):
            if fix:
                new_block2: list[str] = []
                for b in block:
                    is_subgraph = b.lstrip().startswith("subgraph ")
                    if is_subgraph:
                        new_block2.append(b)
                    else:
                        bb = _strip_non_ascii_labels(b)
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

        # NEW: Check for emoji + class suffix combinations (causes parser errors)
        emoji_class_problems = _check_emoji_with_class_suffix(block, start_line_no)
        problems.extend(emoji_class_problems)
        
        # NEW: Check for node with emoji + class suffix
        node_class_problems = _check_node_with_class_suffix(block, start_line_no)
        problems.extend(node_class_problems)
        
        # NEW: Check for unbalanced brackets/braces
        bracket_problems = _check_unbalanced_brackets(block, start_line_no)
        problems.extend(bracket_problems)
        
        # NEW: Check for invalid node IDs
        node_id_problems = _check_invalid_node_ids(block, start_line_no)
        problems.extend(node_id_problems)

        # Check for empty subgraphs (only direction directive, no nodes).
        # Mermaid 11.x rejects these with a syntax error.
        _MERMAID_RESERVED_IDS = {
            'default', 'end', 'graph', 'subgraph', 'style', 'class', 'classdef',
            'click', 'call', 'flowchart', 'sequencediagram', 'gantt', 'pie',
            'linkstyle', 'direction', 'tb', 'lr', 'bt', 'rl', 'td',
            'null', 'true', 'false',
        }
        i_blk = 0
        new_block_sg: list[str] = list(block)
        removed_sg_ids: set[str] = set()
        while i_blk < len(new_block_sg):
            sg_m = re.match(r'(\s*)subgraph (\S+)\[', new_block_sg[i_blk])
            if sg_m:
                sg_id = sg_m.group(2)
                # Collect up to matching 'end'
                depth = 1
                j_blk = i_blk + 1
                while j_blk < len(new_block_sg) and depth > 0:
                    if re.match(r'\s*subgraph ', new_block_sg[j_blk]):
                        depth += 1
                    elif re.match(r'\s*end\s*$', new_block_sg[j_blk]):
                        depth -= 1
                    j_blk += 1
                inner = new_block_sg[i_blk + 1 : j_blk - 1]
                has_nodes = any(
                    ('["' in bl or '-->' in bl)
                    for bl in inner
                    if not re.match(r'\s*(direction\s+|$)', bl.strip())
                )
                if not has_nodes:
                    problems.append(Problem(
                        Path("."), "ERROR",
                        f"Empty Mermaid subgraph '{sg_id}' (contains only direction directive); Mermaid 11.x will reject this",
                        start_line_no + i_blk,
                    ))
                    if fix:
                        removed_sg_ids.add(sg_id)
                        del new_block_sg[i_blk:j_blk]
                        continue
            i_blk += 1
        if removed_sg_ids:
            # Also remove style lines for the removed subgraphs
            new_block_sg = [
                bl for bl in new_block_sg
                if not any(re.search(rf'\bstyle {re.escape(sid)}\b', bl) for sid in removed_sg_ids)
            ]
            block = new_block_sg
            changed = True

        # Check for reserved Mermaid keywords used as subgraph IDs.
        for j, b in enumerate(block):
            sg_id_m = re.match(r'\s*subgraph (\S+)\[', b)
            if sg_id_m:
                sid = sg_id_m.group(1).lower().rstrip('"')
                if sid in _MERMAID_RESERVED_IDS:
                    problems.append(Problem(
                        Path("."), "ERROR",
                        f"Mermaid subgraph uses reserved keyword '{sg_id_m.group(1)}' as ID; prefix with 'nd_'",
                        start_line_no + j,
                    ))

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
