#!/usr/bin/env python3
"""Validate generated Mermaid blocks in markdown files.

Checks performed:
- For each ```mermaid block: counts of 'subgraph' vs 'end' must match.
- Ensures no stray 'end' appears before any 'subgraph' in the same block.
- Reports files and line numbers of issues and exits non-zero if any problems found.
"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"```mermaid(.*?)```", re.DOTALL | re.IGNORECASE)


def check_block(block: str, file: Path, block_index: int):
    lines = block.splitlines()
    subgraph_count = 0
    end_count = 0
    for i, line in enumerate(lines, start=1):
        if re.search(r"\bsubgraph\b", line):
            subgraph_count += 1
        if re.match(r"^\s*end\s*$", line):
            end_count += 1
            # If end appears before any subgraph, that's suspicious
            if end_count > subgraph_count:
                return (False, f"Stray 'end' at line {i} (block {block_index})")
    if subgraph_count != end_count:
        return (False, f"Mismatched counts subgraph={subgraph_count} end={end_count} (block {block_index})")
    return (True, None)


def find_md_files():
    out = []
    for p in ROOT.rglob("*.md"):
        # Only check generated experiment summaries
        if "Output/Learning/experiments" in str(p):
            out.append(p)
    return out


def main():
    files = find_md_files()
    problems = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for idx, m in enumerate(PATTERN.finditer(text), start=1):
            block = m.group(1)
            ok, msg = check_block(block, f, idx)
            if not ok:
                problems.append((f, idx, msg))
    if problems:
        print("Mermaid validation failed:\n")
        for f, idx, msg in problems:
            print(f"File: {f} - block #{idx}: {msg}")
        sys.exit(2)
    print("Mermaid validation passed (no basic structural issues found).")

if __name__ == '__main__':
    main()
