#!/usr/bin/env python3
"""Validate finding and summary formatting.

This is a lightweight stdlib-only checker to prevent regressions in:
- required finding metadata
- score formatting
- forbidden business-impact boilerplate
- Summary/Cloud clickable links (no backticked paths)

Usage:
  python3 Skills/validate_findings.py
  python3 Skills/validate_findings.py --strict
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCORE_RE = re.compile(r"^\s*- \*\*Overall Score:\*\*\s+(🔴|🟠|🟡|🟢)\s+(Critical|High|Medium|Low)\s+(\d{1,2})/10\s*$")
LAST_UPDATED_RE = re.compile(r"^- \U0001f5d3\ufe0f \*\*Last updated:\*\* \d{2}/\d{2}/\d{4} \d{2}:\d{2}\s*$")

# Accept both plain and emoji-prefixed headings.
SUMMARY_H_RE = re.compile(r"^###\s+(?:🧾\s+)?Summary\s*$")
RECS_H_RE = re.compile(r"^###\s+(?:✅\s+)?Recommendations\s*$")


@dataclass
class Problem:
    path: Path
    level: str  # ERROR|WARN
    message: str


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _has_heading(lines: list[str], heading: str) -> bool:
    h = heading.strip()
    return any(l.strip() == h for l in lines)


def _has_heading_re(lines: list[str], pattern: re.Pattern[str]) -> bool:
    return any(pattern.match(l.strip()) for l in lines)


def validate_finding(path: Path, strict: bool) -> list[Problem]:
    lines = _read_lines(path)
    probs: list[Problem] = []

    if not lines or not lines[0].startswith("# "):
        probs.append(Problem(path, "ERROR", "Missing markdown title heading on first line"))
        return probs

    if "- **Description:**" not in "\n".join(lines):
        probs.append(Problem(path, "WARN" if not strict else "ERROR", "Missing - **Description:**"))

    score_line = next((l for l in lines if l.strip().startswith("- **Overall Score:**")), None)
    if not score_line:
        probs.append(Problem(path, "ERROR", "Missing - **Overall Score:**"))
    elif not SCORE_RE.match(score_line):
        probs.append(Problem(path, "ERROR", "Overall Score format should be: - **Overall Score:** 🟠 High 7/10"))

    if not _has_heading_re(lines, SUMMARY_H_RE):
        probs.append(Problem(path, "ERROR", "Missing ### Summary section"))
    else:
        # Check for forbidden prefix (even if author used it).
        joined = "\n".join(lines)
        if re.search(r"###\s+(?:🧾\s+)?Summary\n\s*If not addressed\s*[,\-:]?", joined, flags=re.IGNORECASE):
            probs.append(Problem(path, "ERROR", "Summary must not start with 'If not addressed,'"))
        if "draft finding generated from a title-only input" in joined.lower():
            probs.append(Problem(path, "WARN", "Draft title-only boilerplate still present in Summary"))

    if not _has_heading_re(lines, RECS_H_RE):
        probs.append(Problem(path, "WARN" if not strict else "ERROR", "Missing ### Recommendations section"))

    if not _has_heading(lines, "## 🗺️ Architecture Diagram"):
        probs.append(Problem(path, "WARN" if not strict else "ERROR", "Missing ## 🗺️ Architecture Diagram section"))

    for h in ["## 🤔 Skeptic", "## 🤝 Collaboration", "## Compounding Findings"]:
        if not _has_heading(lines, h):
            probs.append(Problem(path, "WARN" if not strict else "ERROR", f"Missing {h} section"))

    joined = "\n".join(lines).lower()
    if "purpose: review the **security review**" not in joined and _has_heading(lines, "## 🤔 Skeptic"):
        probs.append(Problem(path, "WARN", "Skeptic section missing purpose line; reviewers may default to boilerplate"))

    if _has_heading(lines, "## 🤔 Skeptic") and "what’s missing/wrong vs security review" not in joined:
        probs.append(Problem(path, "WARN", "Skeptic section missing 'What’s missing/wrong vs Security Review' prompt"))

    if not any(l.strip() == "### ⚠️ Assumptions" for l in lines):
        probs.append(
            Problem(
                path,
                "WARN",
                "Missing ### ⚠️ Assumptions section (capture unconfirmed scope/exposure assumptions)",
            )
        )

    if not _has_heading(lines, "## Meta Data"):
        probs.append(Problem(path, "WARN" if not strict else "ERROR", "Missing ## Meta Data section"))
    else:
        last = next((l.strip() for l in lines if "**Last updated:**" in l), "")
        if last and not LAST_UPDATED_RE.match(last):
            probs.append(Problem(path, "WARN" if not strict else "ERROR", "Last updated must be: - 🗓️ **Last updated:** DD/MM/YYYY HH:MM"))
        if not last:
            probs.append(Problem(path, "WARN" if not strict else "ERROR", "Missing - 🗓️ **Last updated:**"))

        # Meta Data should be the final section.
        md_i = next((i for i, l in enumerate(lines) if l.strip() == "## Meta Data"), -1)
        if md_i != -1 and any(l.startswith("## ") for l in lines[md_i + 1 :]):
            probs.append(Problem(path, "WARN" if not strict else "ERROR", "## Meta Data should be the final section"))

    return probs


def validate_cloud_summary(path: Path) -> list[Problem]:
    text = path.read_text(encoding="utf-8", errors="replace")
    probs: list[Problem] = []

    # Backticked paths are not reliably clickable.
    if re.search(r"`\.{0,2}/?\.{0,2}/?Findings/", text):
        probs.append(Problem(path, "ERROR", "Summary uses backticked finding paths; use markdown links instead"))

    # Soft structure checks.
    for h in ["## 🧭 Overview", "## 🚩 Risk", "## ✅ Actions", "## 📌 Findings"]:
        if h not in text:
            probs.append(Problem(path, "WARN", f"Missing heading: {h}"))

    return probs


def iter_md_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob("*.md") if p.is_file() and p.name != ".gitkeep")


def _dedupe_key(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    s = re.sub(r"(/etc/(?:shadow|gshadow|passwd|group))\-(?=\s)", r"\1", s)
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Findings/ and Summary/ formatting")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    problems: list[Problem] = []

    seen_titles: dict[str, Path] = {}
    for sub in ["Cloud", "Code", "Repo"]:
        for f in iter_md_files(ROOT / "Findings" / sub):
            problems.extend(validate_finding(f, strict=args.strict))

            # Detect duplicate findings by title (common when bulk-importing title-only exports).
            lines = _read_lines(f)
            if lines and lines[0].startswith("# "):
                title = lines[0].lstrip("# ").replace("🟣 ", "").strip()
                key = _dedupe_key(title)
                if key in seen_titles:
                    problems.append(
                        Problem(
                            f,
                            "WARN" if not args.strict else "ERROR",
                            f"Duplicate finding title (also in {seen_titles[key].relative_to(ROOT)})",
                        )
                    )
                else:
                    seen_titles[key] = f

    for s in iter_md_files(ROOT / "Summary" / "Cloud"):
        problems.extend(validate_cloud_summary(s))

    errs = [p for p in problems if p.level == "ERROR"]
    warns = [p for p in problems if p.level == "WARN"]

    try:
        for p in errs + warns:
            rel = p.path.relative_to(ROOT)
            print(f"{p.level}: {rel} - {p.message}")
    except BrokenPipeError:
        return 1

    if errs or (args.strict and warns):
        print(f"\nFAILED: {len(errs)} error(s), {len(warns)} warning(s)")
        return 1

    print(f"OK: {len(errs)} error(s), {len(warns)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
