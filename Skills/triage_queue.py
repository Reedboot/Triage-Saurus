#!/usr/bin/env python3
"""List draft findings and what information is missing (stdout-only; no writes).

Goal: provide a deterministic "next work" queue. The AI can then ask the user
targeted questions to complete triage.

Draft/validated status is driven by the explicit `Validation Status` line in
`## Meta Data` when present (preferred), with a fallback to the legacy draft
phrase matching in `Skills/check_draft_findings.py`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from output_paths import OUTPUT_FINDINGS_DIR


ROOT = Path(__file__).resolve().parents[1]

SCORE_RE = re.compile(r"^\s*-\s+\*\*Overall Score:\*\*\s+(🔴|🟠|🟡|🟢)\s+(Critical|High|Medium|Low)\s+(\d{1,2})/10\s*$")


@dataclass(frozen=True)
class FindingRow:
    path: Path
    title: str
    score: int
    severity: str
    is_draft: bool
    missing: list[str]


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _first_line_value(lines: list[str], prefix: str) -> str | None:
    for l in lines:
        if l.strip().startswith(prefix):
            return l.split(prefix, 1)[-1].strip()
    return None


def _parse_title(lines: list[str], path: Path) -> str:
    if not lines:
        return path.stem
    h = lines[0].lstrip("# ").strip()
    return h.replace("🟣", "").strip()


def _parse_score(lines: list[str], path: Path) -> tuple[int, str]:
    for l in lines:
        m = SCORE_RE.match(l.strip())
        if m:
            return int(m.group(3)), m.group(2)
    return 0, "Unknown"


def _explicit_validation_status(lines: list[str]) -> str | None:
    for l in lines:
        if "Validation Status:**" in l:
            return l.split("Validation Status:**", 1)[-1].strip()
    return None


def _is_draft(lines: list[str]) -> bool:
    status = _explicit_validation_status(lines)
    if status:
        if "⚠️ Draft - Needs Triage" in status:
            return True
        if "✅ Validated" in status:
            return False

    blob = "\n".join(lines).lower()
    for ind in [
        "draft finding generated from a title-only input",
        "this is a draft finding",
        "validate the affected resources/scope",
        "title-only input; needs validation",
    ]:
        if ind in blob:
            return True
    return False


def _missing_fields(lines: list[str]) -> list[str]:
    missing: list[str] = []
    joined = "\n".join(lines)

    # Meta data TODOs (common after title-only import).
    for key in ["Provider", "Resource Type", "Category", "Languages", "Source"]:
        if f"- **{key}:** TODO" in joined:
            missing.append(key.lower().replace(" ", "_"))

    # Applicability "Don’t know" is valid, but for "complete" triage the evidence should not be TODO.
    if "- **Evidence:** TODO" in joined:
        missing.append("applicability_evidence")

    # Evidence sections.
    if re.search(r"^###\s+🔎\s+Key Evidence\s*$", joined, flags=re.MULTILINE):
        if re.search(r"^-\s+TODO\s*$", joined, flags=re.MULTILINE):
            missing.append("key_evidence")

    # Common placeholder content.
    if "TODO:" in joined:
        missing.append("todo_placeholders")

    # Last updated missing.
    if "**Last updated:**" not in joined:
        missing.append("last_updated")

    # Validation status missing.
    if "Validation Status:**" not in joined:
        missing.append("validation_status")

    # De-dupe while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for m in missing:
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


def iter_findings() -> list[Path]:
    paths: list[Path] = []
    for sub in ["Cloud", "Code", "Repo"]:
        folder = OUTPUT_FINDINGS_DIR / sub
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            if p.name == ".gitkeep":
                continue
            paths.append(p)
    return paths


def main() -> int:
    p = argparse.ArgumentParser(description="List draft findings and what data is missing (stdout-only).")
    p.add_argument("--limit", type=int, default=20, help="Max draft items to print")
    args = p.parse_args()
    print_queue(limit=args.limit)
    return 0


def print_queue(*, limit: int = 20) -> None:
    rows: list[FindingRow] = []
    for fp in iter_findings():
        lines = _read_lines(fp)
        title = _parse_title(lines, fp)
        score, sev = _parse_score(lines, fp)
        is_draft = _is_draft(lines)
        missing = _missing_fields(lines) if is_draft else []
        rows.append(FindingRow(fp, title, score, sev, is_draft, missing))

    drafts = [r for r in rows if r.is_draft]
    validated = [r for r in rows if not r.is_draft]

    drafts_sorted = sorted(drafts, key=lambda r: (-r.score, r.path.as_posix()))

    print("== Draft triage queue ==")
    print(f"Total findings: {len(rows)}")
    print(f"Draft: {len(drafts)}")
    print(f"Validated: {len(validated)}")

    if not drafts_sorted:
        return 0

    print("\nTop draft items:")
    for r in drafts_sorted[:limit]:
        rel = r.path.relative_to(ROOT)
        miss = ", ".join(r.missing) if r.missing else "(none detected)"
        score = f"{r.severity} {r.score}/10" if r.score else "Unknown"
        print(f"- {score} — {r.title} — {rel} — missing: {miss}")

    print("\nNext step: pick the first item and answer questions to replace TODOs and add evidence.")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
