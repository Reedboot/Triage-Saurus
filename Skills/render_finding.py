#!/usr/bin/env python3
"""Render a finding Markdown file from a JSON "finding model".

Design goal: scripts do repeatable IO + formatting; the AI provides the content.

Usage:
  python3 Skills/render_finding.py --in Output/Audit/RenderInputs/Cloud/Foo.json

Input JSON (v1) minimal shape:
  {
    "version": 1,
    "kind": "cloud" | "code" | "repo",
    "title": "...",
    "description": "...",
    "overall_score": { "severity": "Critical|High|Medium|Low", "score": 1-10 },
    "architecture_mermaid": "flowchart TB\\n  ...",
    "security_review": {
      "summary": "...",
      "applicability": { "status": "Yes|No|Don’t know", "evidence": "..." },
      "key_evidence": ["...", "..."],
      "assumptions": ["...", "..."],
      "exploitability": "...",
      "recommendations": [
        { "text": "...", "score_from": 7, "score_to": 4 }
      ],
      "countermeasures": ["...", "..."],
      "rationale": "..."
    },
    "meta": { "category": "...", "languages": "...", "source": "...", "last_updated": "DD/MM/YYYY HH:MM" },
    "output": { "path": "Output/Findings/Cloud/Foo.md" }  // optional
  }
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from markdown_validator import validate_markdown_file
from output_paths import OUTPUT_FINDINGS_DIR


ROOT = Path(__file__).resolve().parents[1]


def _emoji_for(sev: str) -> str:
    s = sev.strip().lower()
    if s == "critical":
        return "🔴"
    if s == "high":
        return "🟠"
    if s == "medium":
        return "🟡"
    if s == "low":
        return "🟢"
    raise SystemExit(f"Unknown severity: {sev!r} (expected Critical/High/Medium/Low)")


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    parts = [p for p in cleaned.split("_") if p]
    out = "_".join([p[:1].upper() + p[1:] for p in parts]) or "Finding"
    return f"{out}.md"


def _mermaid_block(mermaid: str | None) -> str:
    body = (mermaid or "flowchart TB\n  A[TODO]").rstrip("\n")
    return "```mermaid\n" + body + "\n```"


def _as_list(x: object) -> list[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i).strip()]
    return [str(x)]


def _get_template_path(kind: str) -> Path:
    if kind == "cloud":
        return ROOT / "Templates" / "Render" / "CloudFinding.md"
    if kind == "code":
        return ROOT / "Templates" / "Render" / "CodeFinding.md"
    if kind == "repo":
        return ROOT / "Templates" / "Render" / "RepoFinding.md"
    raise SystemExit(f"Unknown kind for template: {kind}")


def _bullets(items: list[str], *, checkbox: bool = False) -> str:
    if not items:
        return "- (none)"
    prefix = "- [ ] " if checkbox else "- "
    return "\n".join(prefix + i for i in items)


def _recommendations(recs: object, score_i: int) -> str:
    if not isinstance(recs, list) or not recs:
        return f"- [ ] TODO — ⬇️ {score_i}➡️{max(score_i - 2, 0)} (est.)"

    out: list[str] = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        txt = str(r.get("text", "")).strip()
        if not txt:
            continue
        sf = r.get("score_from", score_i)
        st = r.get("score_to", max(score_i - 2, 0))
        try:
            sf_i = int(sf)
            st_i = int(st)
        except Exception:
            sf_i = score_i
            st_i = max(score_i - 2, 0)
        out.append(f"- [ ] {txt} — ⬇️ {sf_i}➡️{st_i} (est.)")
    return "\n".join(out) if out else f"- [ ] TODO — ⬇️ {score_i}➡️{max(score_i - 2, 0)} (est.)"


def render_md(model: dict) -> str:
    version = int(model.get("version", 1))
    if version != 1:
        raise SystemExit(f"Unsupported model version: {version}")

    kind = str(model.get("kind", "")).strip().lower()
    if kind not in {"cloud", "code", "repo"}:
        raise SystemExit("Missing/invalid 'kind' (cloud|code|repo)")

    title = str(model.get("title", "")).strip()
    if not title:
        raise SystemExit("Missing 'title'")

    description = str(model.get("description", "")).strip() or title

    overall = model.get("overall_score") or {}
    sev = str(overall.get("severity", "")).strip()
    score = overall.get("score", None)
    if score is None:
        raise SystemExit("Missing overall_score.score")
    score_i = int(score)
    if score_i < 1 or score_i > 10:
        raise SystemExit("overall_score.score must be 1-10")
    emoji = _emoji_for(sev)

    arch_mermaid = str(model.get("architecture_mermaid", "")).strip()
    if not arch_mermaid:
        arch_mermaid = "flowchart TB\n  A[TODO]"

    sr = model.get("security_review") or {}
    sr_summary = str(sr.get("summary", "")).strip() or "TODO: Provide a non-boilerplate summary."
    app = sr.get("applicability") or {}
    app_status = str(app.get("status", "Don’t know")).strip() or "Don’t know"
    app_evidence = str(app.get("evidence", "")).strip() or "TODO"

    key_evidence = _as_list(sr.get("key_evidence"))
    assumptions = _as_list(sr.get("assumptions"))
    exploitability = str(sr.get("exploitability", "")).strip() or "TODO"
    rationale = str(sr.get("rationale", "")).strip() or "TODO"

    recommendations_block = _recommendations(sr.get("recommendations"), score_i)
    countermeasures_block = _bullets(_as_list(sr.get("countermeasures")))

    overview_bullets = _bullets(_as_list(model.get("overview_bullets")))
    risks_bullets = _bullets(_as_list(sr.get("risks")))
    # Repo findings often separate deep-dive evidence; allow both schema shapes.
    key_evidence_deep = _as_list(sr.get("key_evidence_deep") or sr.get("key_evidence_deep_dive"))
    if not key_evidence_deep:
        key_evidence_deep = key_evidence
    key_evidence_deep_bullets = _bullets(key_evidence_deep) if key_evidence_deep else "- TODO"

    meta = model.get("meta") or {}
    category = str(meta.get("category", "")).strip() or "TODO"
    languages = str(meta.get("languages", "")).strip() or "Unknown"
    source = str(meta.get("source", "")).strip() or "Unknown"
    validation_status = str(meta.get("validation_status", "")).strip() or "⚠️ Draft - Needs Triage"
    last_updated = str(meta.get("last_updated", "")).strip()
    if not last_updated:
        raise SystemExit("Missing meta.last_updated (expected DD/MM/YYYY HH:MM)")

    template_path = _get_template_path(kind)
    if not template_path.is_file():
        raise SystemExit(f"Renderer template not found: {template_path.relative_to(ROOT)}")

    tmpl = template_path.read_text(encoding="utf-8", errors="replace")

    provider = str(meta.get("provider", "")).strip() or str(model.get("provider", "")).strip() or "TODO"
    resource_type = str(meta.get("resource_type", "")).strip() or str(model.get("resource_type", "")).strip() or "TODO"

    mapping: dict[str, str] = {
        "title": title,
        "description": description,
        "overall_score_emoji": emoji,
        "overall_score_severity": sev,
        "overall_score": str(score_i),
        "architecture_mermaid": arch_mermaid.rstrip("\n"),
        "security_review_summary": sr_summary,
        "applicability_status": app_status,
        "applicability_evidence": app_evidence,
        "assumptions_bullets": _bullets(assumptions),
        "key_evidence_bullets": _bullets(key_evidence) if key_evidence else "- TODO",
        "key_evidence_deep_bullets": key_evidence_deep_bullets,
        "exploitability": exploitability,
        "recommendations_checkboxes": recommendations_block,
        "countermeasures_bullets": countermeasures_block,
        "rationale": rationale,
        "overview_bullets": overview_bullets,
        "risks_bullets": risks_bullets,
        "category": category,
        "languages": languages,
        "validation_status": validation_status,
        "source": source,
        "last_updated": last_updated,
        "provider": provider,
        "resource_type": resource_type,
    }

    out = tmpl
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", v)

    # Fail if any placeholders remain to avoid silent partial renders.
    if re.search(r"\{\{[A-Za-z0-9_]+\}\}", out):
        raise SystemExit(f"Unresolved placeholders remain after render using {template_path.relative_to(ROOT)}")

    return out.rstrip() + "\n"


def compute_output_path(model: dict, *, in_path: Path) -> Path:
    out = (model.get("output") or {}).get("path")
    if out:
        p = Path(str(out))
        if not p.is_absolute():
            return (ROOT / p).resolve()
        return p

    kind = str(model.get("kind", "")).strip().lower()
    title = str(model.get("title", "")).strip()

    sub = "Cloud" if kind == "cloud" else ("Code" if kind == "code" else "Repo")
    return (OUTPUT_FINDINGS_DIR / sub / _safe_filename(title)).resolve()


def main() -> int:
    p = argparse.ArgumentParser(description="Render a finding Markdown file from JSON.")
    p.add_argument("--in", dest="in_path", required=True, help="Input JSON path")
    p.add_argument("--out", dest="out_path", help="Override output Markdown path")
    args = p.parse_args()

    in_path = Path(args.in_path).expanduser()
    if not in_path.is_absolute():
        in_path = (ROOT / in_path).resolve()
    if not in_path.is_file():
        raise SystemExit(f"Input not found: {in_path}")

    model = json.loads(in_path.read_text(encoding="utf-8", errors="replace"))
    md = render_md(model)

    out_path = Path(args.out_path).expanduser() if args.out_path else compute_output_path(model, in_path=in_path)
    if not out_path.is_absolute():
        out_path = (ROOT / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    probs = validate_markdown_file(out_path, fix=True)
    errs = [pr for pr in probs if pr.level == "ERROR"]
    if errs:
        raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")

    try:
        rel = out_path.relative_to(ROOT)
        print(f"Wrote: {rel}")
    except ValueError:
        print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
