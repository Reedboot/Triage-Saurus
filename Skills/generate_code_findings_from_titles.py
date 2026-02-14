#!/usr/bin/env python3
"""Generate draft Code findings from title-only or minimal inputs.

This is the Code analogue to `generate_findings_from_titles.py` (Cloud).
It helps quickly turn an `Intake/` folder into well-formed Markdown findings
under `Output/Findings/Code/` so the risk register generator can run.

Inputs
- A folder containing files (recursively):
  - 1 file per finding: extracts title from the first non-empty line.
  - If present, extracts `- **Description:** ...` as a helpful seed.
  - `.txt` / `.csv`: treated as 1 finding per line (entire line is the title).

Outputs
- Draft finding Markdown files in the output folder you specify.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from pathlib import Path

from output_paths import OUTPUT_RENDER_INPUTS_DIR
from markdown_validator import validate_markdown_file

ROOT = Path(__file__).resolve().parents[1]


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


def _normalise_title(line: str) -> str:
    # Accept both raw lines and Markdown headings.
    return line.strip().lstrip("\ufeff").lstrip("# ").strip()


def titlecase_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    parts = [p for p in cleaned.split("_") if p]
    return "_".join([p[:1].upper() + p[1:] for p in parts]) or "Finding"


def _unique_out_path(out_dir: Path, base: str) -> Path:
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
    if score >= 8:
        return "🔴 Critical"
    if score >= 6:
        return "🟠 High"
    if score >= 4:
        return "🟡 Medium"
    return "🟢 Low"


OWASP_2021 = {
    "broken access control": ("A01", 8),
    "cryptographic failures": ("A02", 7),
    "injection": ("A03", 8),
    "insecure design": ("A04", 6),
    "security misconfiguration": ("A05", 6),
    "vulnerable and outdated components": ("A06", 7),
    "identification and authentication failures": ("A07", 7),
    "software and data integrity failures": ("A08", 6),
    "security logging and monitoring failures": ("A09", 5),
    "server-side request forgery": ("A10", 7),
}


def owasp_id_and_score_for(title: str) -> tuple[str | None, int]:
    key = re.sub(r"\s+", " ", title.strip().lower())
    match = OWASP_2021.get(key)
    if match:
        return match
    # Default: mid-risk draft until validated in a real codebase.
    return None, 5


def extract_title_and_description(path: Path) -> tuple[str | None, str]:
    """Return (title, description). Title is required; description is optional."""
    ext = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")

    if ext in {".txt", ".csv"}:
        # Caller handles list expansion; keep this function file-based.
        return None, ""

    title = None
    description = ""

    for line in text.splitlines():
        t = _normalise_title(line)
        if t:
            title = t
            break

    for line in text.splitlines():
        if line.strip().startswith("- **Description:**"):
            description = line.replace("- **Description:**", "").strip()
            break

    return title, description


def write_finding(out_path: Path, title: str, description: str, score: int, ts: str, source_path: Path) -> None:
    sev = severity(score)
    owasp_id, _ = owasp_id_and_score_for(title)

    # Keep the displayed title readable; IDs stay as a prefix to help sorting.
    display_title = f"{owasp_id} {title}" if owasp_id else title

    # Use a generic architecture; real diagrams belong in repo-specific findings.
    diagram = """```mermaid
flowchart TB
  User[🧑‍💻 User] --> App[🧩 App/API]
  App --> Dep[🧩 Dependency]
  App --> Data[🗄️ Data]

  Sec[🛡️ Controls] -.-> App
```"""

    draft_note = "Draft finding generated from a title-only input; needs validation."

    # The risk register generator requires the Summary section to exist and contain text.
    summary = description or draft_note

    content = f"""# 🟣 {display_title}

## 🗺️ Architecture Diagram
{diagram}

- **Description:** {description or draft_note} {draft_note if description else ""}
- **Overall Score:** {sev} {score}/10

## 🛡️ Security Review
### 🧾 Summary
{summary}

{draft_note}

### ✅ Applicability
- **Status:** Don’t know
- **Evidence:** Sample input only; requires confirmation in the target repo.

### 🔎 Key Evidence
- **Source:** `{source_path.relative_to(ROOT)}`

### ⚠️ Assumptions
- Unconfirmed: The pattern is reachable in a production deployment.
- Unconfirmed: Exploitability is not mitigated by central middleware/edge controls.

### 🎯 Exploitability
Unknown from sample input alone; depends on reachable endpoints, authn/authz controls, and data sensitivity.

### ✅ Recommendations
- [ ] Confirm whether this pattern exists in the target repo (search for the relevant sinks/sources) — ⬇️ {score}➡️{score} (est.)
- [ ] Add/strengthen automated tests (unit/integration) to prevent regression — ⬇️ {score}➡️{max(score - 2, 0)} (est.)
- [ ] Add runtime detection where applicable (logging/alerts, WAF rules) — ⬇️ {score}➡️{max(score - 1, 0)} (est.)

### 🧰 Considered Countermeasures
- 🟡 Secure coding standards and review gates — reduces likelihood, does not eliminate existing issues.
- 🟡 Central auth middleware — effective if uniformly enforced.
- 🟢 Automated tests — prevents reintroduction after fix.

### 📐 Rationale
This is a generic draft based on minimal input. Validate affected components, exploitability, and impact before final scoring.

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** <fill in>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **Mitigation note:** Identify the concrete code path(s) and add tests around the intended access/validation logic.

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** <fill in>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **Mitigation note:** If applicable, ensure edge controls (WAF/API gateway) and logging support detection during rollout.

## 🤝 Collaboration
- **Outcome:** Draft created for triage.
- **Next step:** Validate in a real repo and refine scope/evidence.

## Compounding Findings
- **Compounds with:** None identified

## Meta Data
<!-- Meta Data must remain the final section in the file. -->
- **Category:** OWASP Top 10 2021 ({owasp_id or "Unknown"})
- **Languages:** Unknown
- **Source:** Sample finding
- 🗓️ **Last updated:** {ts}
"""

    out_path.write_text(content, encoding="utf-8")

    probs = validate_markdown_file(out_path, fix=True)
    errs = [p for p in probs if p.level == "ERROR"]
    if errs:
        raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")


def _render_json_dir(kind: str) -> Path:
    base = OUTPUT_RENDER_INPUTS_DIR / kind.title()
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_finding_model(*, display_title: str, description: str, score: int, ts: str, source_path: Path) -> dict:
    # display_title is what appears after "# 🟣".
    sev = severity(score).split(" ", 1)[-1]
    return {
        "version": 1,
        "kind": "code",
        "title": display_title,
        "description": description or "Draft finding generated from a title-only input; needs validation.",
        "overall_score": {"severity": sev, "score": score},
        "architecture_mermaid": "flowchart TB\n  User[🧑‍💻 User] --> App[🧩 App/API]\n  App --> Dep[🧩 Dependency]\n  App --> Data[🗄️ Data]\n\n  Sec[🛡️ Controls] -.-> App",
        "security_review": {
            "summary": description or "TODO: Provide a non-boilerplate summary.",
            "applicability": {"status": "Don’t know", "evidence": "Title-only input; needs validation."},
            "key_evidence": [f"**Source:** `{source_path.relative_to(ROOT)}`"],
            "assumptions": [
                "Unconfirmed: The pattern is reachable in a production deployment.",
                "Unconfirmed: Exploitability is not mitigated by central middleware/edge controls.",
            ],
            "exploitability": "Unknown from sample input alone; depends on reachable endpoints, authn/authz controls, and data sensitivity.",
            "recommendations": [
                {"text": "Confirm whether this pattern exists in the target repo (search for the relevant sinks/sources)", "score_from": score, "score_to": score},
                {"text": "Add/strengthen automated tests (unit/integration) to prevent regression", "score_from": score, "score_to": max(score - 2, 0)},
            ],
            "countermeasures": [
                "🟡 Secure coding standards and review gates — reduces likelihood, does not eliminate existing issues.",
                "🟡 Central auth middleware — effective if uniformly enforced.",
                "🟢 Automated tests — prevents reintroduction after fix.",
            ],
            "rationale": "This is a generic draft based on minimal input. Validate affected components, exploitability, and impact before final scoring.",
        },
        "meta": {
            "category": "OWASP Top 10 (draft)",
            "languages": "Unknown",
            "source": "Title-only intake import",
            "validation_status": "⚠️ Draft - Needs Triage",
            "last_updated": ts,
        },
    }


def iter_input_paths(in_path: Path) -> list[Path]:
    if in_path.is_file():
        return [in_path]
    return sorted(p for p in in_path.rglob("*") if p.is_file())


def titles_from_list_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    titles = [_normalise_title(l) for l in text.splitlines()]
    return [t for t in titles if t]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate draft code findings from minimal/title-only inputs.")
    parser.add_argument("--in-dir", required=True, help="Input folder (recursively) or a single .txt/.csv list file")
    parser.add_argument("--out-dir", required=True, help="Output folder for generated findings")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing findings that match the computed output filename",
    )
    parser.add_argument(
        "--emit-render-json",
        action="store_true",
        help="Also write JSON render inputs under Output/Audit/RenderInputs/ (repro/debug only).",
    )
    args = parser.parse_args()

    in_path = (ROOT / args.in_dir).resolve() if not Path(args.in_dir).is_absolute() else Path(args.in_dir)
    out_dir = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)

    if not in_path.exists():
        raise SystemExit(f"Input path not found: {in_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = now_uk()

    generated = 0
    skipped_existing = 0
    skipped_existing_examples: list[str] = []
    preexisting_paths = set(sorted(out_dir.glob("*.md"))) if out_dir.exists() else set()
    paths = iter_input_paths(in_path)

    for path in paths:
        if path.name.startswith("."):
            continue
        if path.name in {"README.md", ".gitignore", ".gitkeep"}:
            continue

        ext = path.suffix.lower()
        if ext in {".txt", ".csv"}:
            for title in titles_from_list_file(path):
                owasp_id, score = owasp_id_and_score_for(title)
                base = titlecase_filename(f"{owasp_id}_{title}" if owasp_id else title)
                out_path = out_dir / f"{base}.md"
                if out_path.exists() and not args.overwrite_existing:
                    if out_path in preexisting_paths:
                        skipped_existing += 1
                        if len(skipped_existing_examples) < 10:
                            skipped_existing_examples.append(f"{path} -> {out_path.name}")
                        if args.emit_render_json:
                            display_title = f"{owasp_id} {title}" if owasp_id else title
                            model = build_finding_model(
                                display_title=display_title,
                                description="",
                                score=score,
                                ts=ts,
                                source_path=path,
                            )
                            model["output"] = {"path": str(out_path.relative_to(ROOT))}
                            json_dir = _render_json_dir("Code")
                            json_path = json_dir / out_path.with_suffix(".json").name
                            json_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                        continue
                    out_path = _unique_out_path(out_dir, base)
                if not out_path.exists():
                    out_path = _unique_out_path(out_dir, base)
                write_finding(out_path, title, "", score, ts, path)
                if args.emit_render_json:
                    display_title = f"{owasp_id} {title}" if owasp_id else title
                    model = build_finding_model(
                        display_title=display_title,
                        description="",
                        score=score,
                        ts=ts,
                        source_path=path,
                    )
                    model["output"] = {"path": str(out_path.relative_to(ROOT))}
                    json_dir = _render_json_dir("Code")
                    json_path = json_dir / out_path.with_suffix(".json").name
                    json_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                generated += 1
            continue

        title, description = extract_title_and_description(path)
        if not title:
            continue
        owasp_id, score = owasp_id_and_score_for(title)
        base = titlecase_filename(f"{owasp_id}_{title}" if owasp_id else title)
        out_path = out_dir / f"{base}.md"
        if out_path.exists() and not args.overwrite_existing:
            if out_path in preexisting_paths:
                skipped_existing += 1
                if len(skipped_existing_examples) < 10:
                    skipped_existing_examples.append(f"{path} -> {out_path.name}")
                if args.emit_render_json:
                    display_title = f"{owasp_id} {title}" if owasp_id else title
                    model = build_finding_model(
                        display_title=display_title,
                        description=description,
                        score=score,
                        ts=ts,
                        source_path=path,
                    )
                    model["output"] = {"path": str(out_path.relative_to(ROOT))}
                    json_dir = _render_json_dir("Code")
                    json_path = json_dir / out_path.with_suffix(".json").name
                    json_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                continue
            out_path = _unique_out_path(out_dir, base)
        if not out_path.exists():
            out_path = _unique_out_path(out_dir, base)
        write_finding(out_path, title, description, score, ts, path)
        if args.emit_render_json:
            display_title = f"{owasp_id} {title}" if owasp_id else title
            model = build_finding_model(
                display_title=display_title,
                description=description,
                score=score,
                ts=ts,
                source_path=path,
            )
            model["output"] = {"path": str(out_path.relative_to(ROOT))}
            json_dir = _render_json_dir("Code")
            json_path = json_dir / out_path.with_suffix(".json").name
            json_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        generated += 1

    msg = f"Generated {generated} finding(s) into {out_dir}"
    if skipped_existing:
        msg += f" (skipped {skipped_existing} existing output file(s))"
    print(msg)
    if skipped_existing:
        print("Note: Some findings were not generated because the output file already exists.")
        print("      Re-run with `--overwrite-existing` if you intended to regenerate outputs.")
        if skipped_existing_examples:
            print("Examples (input -> existing output):")
            for ex in skipped_existing_examples:
                print(f"  - {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
