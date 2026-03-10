#!/usr/bin/env python3
"""Generate a lightweight repo summary markdown from Cozo scan data.

This enhanced summary will embed relevant Mermaid architecture diagrams
from Output/Summary/Cloud/Architecture_<PROVIDER>.md when available. The
generator selects diagrams for providers detected in the findings. If the
findings are labelled with `terraform` the generator will attempt to include
all available architecture diagrams.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Iterable, Tuple

from cozo_helpers import fetch_findings_with_context

SEVERITY_ORDER = {"error": 1, "warning": 2, "info": 3}

PROVIDER_TO_ARCH = {
    "azure": "Architecture_Azure.md",
    "aws": "Architecture_AWS.md",
    "gcp": "Architecture_GCP.md",
    "oracle": "Architecture_ORACLE.md",
    "alicloud": "Architecture_ALICLOUD.md",
}


def _sanitize_repo_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return cleaned or "repo_summary"


def _context_summary(contexts: List[Dict[str, str]], limit: int = 2) -> str:
    snippets = []
    for entry in contexts[:limit]:
        key = entry.get("context_key")
        value = entry.get("context_value")
        if key and value:
            snippets.append(f"{key}={value}")
    return "; ".join(snippets) or "—"


def _sort_key(row: Dict[str, str]) -> int:
    severity = str(row.get("severity") or "").strip().lower()
    return SEVERITY_ORDER.get(severity, 99)


def _highlight_internet_ingress(nodes: List[Dict[str, str]]) -> List[str]:
    highlights = []
    for node in nodes:
        if node.get("internet_ingress") == "True" or node.get("internet_ingress") is True:
            highlights.append(f'{node.get("finding_id")}: style {node.get("finding_id")},fill:#ffcccc,stroke:#ff0000,stroke-width:2')
            highlights.append(f'{node.get("finding_id")} --> Internet')
    return highlights

def _extract_mermaid_blocks(md_text: str) -> List[str]:
    """Return a list of mermaid fenced code blocks (including fences).

    Matches fences that start with ```mermaid (optionally followed by a language)
    and end with ```.
    """
    blocks = []
    for m in re.finditer(r"```mermaid(?:\s.*)?\n(.*?)\n```", md_text, flags=re.S | re.I):
        inner = m.group(1).strip('\n')
        blocks.append("```mermaid\n" + inner + "\n```")
    return blocks


def _collect_architecture_blocks(providers: Iterable[str], cloud_dir: Path) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    provs = set(p.lower() for p in providers)

    # If terraform is present, include all available architecture files
    if "terraform" in provs:
        for path in sorted(cloud_dir.glob("Architecture_*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for block in _extract_mermaid_blocks(text):
                blocks.append((path.stem.replace('Architecture_', ''), block))
        return blocks

    for prov in provs:
        fname = PROVIDER_TO_ARCH.get(prov)
        if not fname:
            # try direct mapping like 'aws' -> Architecture_AWS.md
            fname = f"Architecture_{prov.upper()}.md"
        path = cloud_dir / fname
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for block in _extract_mermaid_blocks(text):
            blocks.append((prov, block))
    return blocks


def build_summary_markdown(
    repo_name: str,
    scan_id: str,
    findings: List[Dict[str, str]],
    context_map: Dict[str, List[Dict[str, str]]],
) -> str:
    providers = Counter((row.get("provider") or "unknown").lower() for row in findings)
    severities = Counter((row.get("severity") or "unknown").upper() for row in findings)
    total = len(findings)

    lines: List[str] = [
        f"# Repository summary — {repo_name}",
        "",
        f"* Scan ID: `{scan_id}`",
        f"* Findings captured: {total}",
    ]
    if providers:
        lines.append(
            "* Providers: "
            + ", ".join(f"{prov} ({count})" for prov, count in providers.most_common())
        )
    if severities:
        lines.append(
            "* Severity mix: "
            + ", ".join(f"{sev} ({count})" for sev, count in severities.items())
        )
    lines.append("")

    # Embed architecture diagrams if available for detected providers
    cloud_dir = Path("Output") / "Summary" / "Cloud"
    arch_blocks = _collect_architecture_blocks(providers.keys(), cloud_dir)
    if arch_blocks:
        lines.append("## Architecture")
        lines.append("")
        # Highlight internet ingress in Mermaid diagrams
        ingress_highlights = _highlight_internet_ingress(findings)
        for prov, block in arch_blocks:
            lines.append(f"### {prov.capitalize()}")
            lines.append("")
            if ingress_highlights:
                # Insert highlights at the end of the mermaid block
                block = block.rstrip('`') + '\n' + '\n'.join(ingress_highlights) + '\n```'
            lines.append(block)
            lines.append("")

    # Key Evidence (top 3 findings)
    top_n = 3
    top_findings = sorted(findings, key=_sort_key)[:top_n]
    evidence_lines: List[str] = []

    def _lang_from_path(p: str) -> str:
        ext = Path(p).suffix.lower()
        return {
            ".py": "python",
            ".tf": "hcl",
            ".tfvars": "hcl",
            ".js": "javascript",
            ".ts": "typescript",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".json": "json",
            ".sh": "bash",
        }.get(ext, "")

    def _snippet_from_file(path: str, line: str | int, context: int = 2) -> str | None:
        try:
            ln = int(line)
        except Exception:
            return None
        try:
            p = Path(path)
            if not p.exists():
                return None
            lines_all = p.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(0, ln - 1 - context)
            end = min(len(lines_all), ln - 1 + context + 1)
            snippet = "\n".join(lines_all[start:end])
            return snippet
        except Exception:
            return None

    for f in top_findings:
        rule_id = f.get("rule_id") or f.get("check_id") or "unknown"
        severity = f.get("severity") or "unknown"
        source_file = f.get("source_file") or "unknown"
        lineno = f.get("start_line") or "0"
        loc = f"{source_file}:{lineno}"
        ctx = _context_summary(context_map.get(f.get("finding_id") or "", []))
        evidence_lines.append(f"- **{rule_id}** — {severity} — `{loc}` — {ctx}")
        snippet = _snippet_from_file(source_file, lineno)
        if snippet:
            lang = _lang_from_path(source_file)
            fence = f"```{lang}" if lang else "```"
            evidence_lines.append(fence)
            evidence_lines.append(snippet)
            evidence_lines.append("```")

    if evidence_lines:
        lines.append("## 💡 Key Evidence")
        lines.append("")
        lines.extend(evidence_lines)
        lines.append("")

    lines.append("## Findings")
    lines.append("| Rule | Severity | Provider | Location | Context |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in sorted(findings, key=_sort_key):
        rule_id = row.get("rule_id") or row.get("check_id") or "unknown"
        severity = row.get("severity") or "unknown"
        provider = row.get("provider") or "unknown"
        source_file = row.get("source_file") or "unknown"
        lineno = row.get("start_line") or "0"
        location = f"{source_file}:{lineno}"
        context = _context_summary(context_map.get(row.get("finding_id") or "", []))
        lines.append(f"| `{rule_id}` | {severity} | {provider} | `{location}` | {context} |")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repository name (used for summary filename).")
    parser.add_argument("--scan-id", required=True, help="Scan identifier stored in Cozo.")
    parser.add_argument(
        "--output-dir",
        default="Output/Summary/Repos",
        help="Directory where the summary file will be written.",
    )
    args = parser.parse_args()

    findings, context_map = fetch_findings_with_context(scan_id=args.scan_id)
    if not findings:
        print(f"No findings found in Cozo for scan {args.scan_id}; skipping summary.")
        return 0

    summary = build_summary_markdown(args.repo, args.scan_id, findings, context_map)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_sanitize_repo_name(args.repo)}.md"
    out_path.write_text(summary, encoding="utf-8")

    print(f"Repo summary generated: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
