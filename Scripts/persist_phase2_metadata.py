#!/usr/bin/env python3
"""Persist Phase 2 context metadata into the experiment database."""

import argparse
import re
from pathlib import Path
from typing import Iterable, Dict, List

from db_helpers import upsert_context_metadata
from output_paths import OUTPUT_ROOT


TLDR_KEYS = {
    "Languages": "tldr.languages",
    "Frameworks": "tldr.frameworks",
    "Containerization": "tldr.containerization",
    "Hosting": "tldr.hosting",
    "CI/CD": "tldr.ci_cd",
    "Security Status": "tldr.security_status",
}

SECTION_HEADERS = {
    "authentication": "### 🔐 Authentication & Identity",
    "network_topology": "### 🌐 Network Topology",
    "ingress_paths": "## 🔌 Ingress Paths",
    "egress_paths": "## 🔌 Egress Paths",
    "container_notes": "## 🐳 Container & Deployment Notes",
}


def parse_tldr(lines: Iterable[str]) -> Dict[str, str]:
    data: Dict[str, str] = {}
    in_tldr = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## 📊 TL;DR"):
            in_tldr = True
            continue

        if not in_tldr:
            continue

        if not stripped:
            if data:
                break
            continue

        if stripped.startswith("|"):
            match = re.match(r"\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|", stripped)
            if match:
                key = match.group(1).strip()
                val = match.group(2).strip()
                if val and key not in data:
                    data[key] = val
            continue

        if stripped.startswith("**Top Risks:**"):
            break

    return data


def parse_top_risks(lines: Iterable[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**Top Risks:**"):
            return stripped.split("**Top Risks:**", 1)[1].strip()
    return None


def collect_section(lines: List[str], header: str) -> List[str]:
    collected: List[str] = []
    start_index = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start_index = idx
            break
    if start_index is None:
        return collected

    for line in lines[start_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            break
        if stripped.startswith("### "):
            break
        text = None
        if stripped.startswith("+- "):
            text = stripped[3:].strip()
        elif stripped.startswith("- "):
            text = stripped[2:].strip()
        elif stripped.startswith("+ "):
            text = stripped[2:].strip()
        if text:
            collected.append(text)
    return collected


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def build_metadata(lines: List[str]) -> Dict[str, str]:
    metadata: Dict[str, str] = {}

    tldr = parse_tldr(lines)
    if tldr:
        for label, key in TLDR_KEYS.items():
            value = tldr.get(label)
            if value:
                metadata[key] = value

    top_risks = parse_top_risks(lines)
    if top_risks:
        metadata["tldr.top_risks"] = top_risks

    for slug, header in SECTION_HEADERS.items():
        bullets = collect_section(lines, header)
        if bullets:
            metadata[f"section.{slug}"] = "\n".join(bullets)

    return metadata


def find_summary_path(experiment_id: str, repo_name: str) -> Path:
    experiments_root = OUTPUT_ROOT / "Learning" / "experiments"
    candidates = list(experiments_root.glob(f"{experiment_id}_*"))
    if not candidates:
        raise FileNotFoundError(f"No experiment directory found for ID {experiment_id}")

    exp_dir = candidates[0]
    summary_path = exp_dir / "Summary" / "Repos" / f"{repo_name}.md"
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary not found at {summary_path}")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist Phase 2 context metadata.")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--repo", required=True, help="Repository name (folder)")
    parser.add_argument(
        "--namespace",
        default="phase2_summary",
        help="Metadata namespace (default: phase2_summary)",
    )
    parser.add_argument(
        "--source",
        default="phase2_summary",
        help="Source label stored with metadata (default: phase2_summary)",
    )
    parser.add_argument(
        "--summary-path",
        help="Override path to the repo summary Markdown file",
    )

    args = parser.parse_args()

    if args.summary_path:
        summary = Path(args.summary_path)
    else:
        summary = find_summary_path(args.experiment, args.repo)

    lines = summary.read_text(encoding="utf-8").splitlines()
    metadata = build_metadata(lines)
    if not metadata:
        print("No metadata extracted from summary document.")
        return 1

    for key, value in metadata.items():
        upsert_context_metadata(
            experiment_id=args.experiment,
            repo_name=args.repo,
            key=key,
            value=value,
            namespace=args.namespace,
            source=args.source,
        )
        print(f"Stored metadata: {key}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
