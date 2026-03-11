#!/usr/bin/env python3
"""Render a finding Markdown file from a Cozo finding captured in Output/Data/cozo.db."""

import argparse
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, StrictUndefined

from cozo_helpers import clamp_score, get_finding_with_context, severity_summary
from markdown_validator import validate_markdown_file
from output_paths import OUTPUT_FINDINGS_DIR

ROOT = Path(__file__).resolve().parents[1]

# (The rest of the helper functions from the original script will be kept the same)
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


class ScoreWrapper:
    def __init__(self, score: int, severity: str) -> None:
        self.score = score
        self.severity = severity

    def __str__(self) -> str:
        return str(self.score)

    def __int__(self) -> int:
        return self.score

    def __float__(self) -> float:
        return float(self.score)

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


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _humanize_rule_id(rule_id: str | None) -> str:
    if not rule_id:
        return "Finding"
    cleaned = re.sub(r"[_\-.]+", " ", rule_id).strip()
    parts = [part.capitalize() for part in cleaned.split() if part]
    return " ".join(parts) or "Finding"


def _format_context_lines(contexts: list[dict[str, Any]], limit: int = 4) -> list[str]:
    lines: list[str] = []
    for entry in contexts:
        key = entry.get("context_key")
        value = entry.get("context_value")
        if not key or not value:
            continue
        snippet = f"{key}: {value}"
        lines.append(snippet)
        if len(lines) >= limit:
            break
    return lines


def _normalize_recommendations(raw: Any, score_i: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, Mapping):
            text = str(
                item.get("text")
                or item.get("summary")
                or item.get("title")
                or item.get("name")
                or "TODO: add recommendation"
            ).strip()
            try:
                score_from = int(item.get("score_from", score_i))
            except Exception:
                score_from = score_i
            try:
                score_to = int(item.get("score_to", max(score_i - 2, 0)))
            except Exception:
                score_to = max(score_i - 2, 0)
        else:
            text = str(item).strip() or "TODO: add recommendation"
            score_from = score_i
            score_to = max(score_i - 2, 0)
        candidates.append(
            {
                "text": text,
                "score_from": score_from,
                "score_to": score_to,
            }
        )

    if not candidates:
        candidates.append(
            {
                "text": "TODO: add recommendation",
                "score_from": score_i,
                "score_to": max(score_i - 2, 0),
            }
        )
    return candidates


def _default_skeptic_block() -> dict[str, dict[str, str]]:
    return {
        "dev": {
            "missing": "Nothing yet — awaiting a security review narrative.",
            "score_recommendation": "✅ Keep",
            "how_it_could_be_worse": "TODO: describe escalation path.",
            "countermeasure_effectiveness": "TODO: explain how controls reduce risk.",
            "assumptions_to_validate": "None yet.",
        },
        "platform": {
            "missing": "TODO: platform constraints / risks.",
            "score_recommendation": "✅ Keep",
            "operational_constraints": "TODO: describe deployment constraints.",
            "countermeasure_effectiveness": "TODO: describe operational controls.",
            "assumptions_to_validate": "None yet.",
        },
    }


def _extract_template_body(text: str) -> str:
    match = re.search(
        r"## File Template\b\s*(?:```|~~~)md\s*\n(.*?)(?:\n```|\n~~~)\s*(?:## Required Sections\b|\n## Testing\b|\Z)",
        text,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return text.strip()


def _build_render_context(model: dict[str, Any], score_wrapper: ScoreWrapper, emoji: str) -> dict[str, Any]:
    context: dict[str, Any] = dict(model)
    context.setdefault("security_review_summary", (model.get("security_review") or {}).get("summary", ""))
    context.setdefault(
        "applicability_evidence",
        (model.get("security_review") or {}).get("applicability", {}).get("evidence", ""),
    )
    context.setdefault("recommendations_checkboxes", "")
    context.setdefault("countermeasures_bullets", "")
    context.setdefault("assumptions_bullets", "")
    context.setdefault("key_evidence_bullets", "")
    context.setdefault("compounding_findings", [])
    context.setdefault("collaboration", {"outcome": "Pending collaboration", "next_step": "TBD"})
    context.setdefault("skeptic", _default_skeptic_block())
    context["overall_score"] = score_wrapper
    context["overall_score_severity"] = score_wrapper.severity
    context["overall_score_emoji"] = emoji
    context["overall_score_value"] = int(score_wrapper)
    meta = model.get("meta") or {}
    context.setdefault("last_updated", meta.get("last_updated") or datetime.now().strftime("%d/%m/%Y %H:%M"))
    return context

def _get_template_path(kind: str) -> Path:
    if kind == "cloud":
        return ROOT / "Templates" / "CloudFinding.md"
    if kind == "code":
        return ROOT / "Templates" / "CodeFinding.md"
    if kind == "repo":
        return ROOT / "Templates" / "RepoFinding.md"
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

    template_path = _get_template_path(kind)
    if not template_path.is_file():
        raise SystemExit(f"Renderer template not found: {template_path.relative_to(ROOT)}")

    overall = model.get("overall_score") or {}
    severity = str(overall.get("severity", "")).strip()
    score_value = overall.get("score", None)
    if score_value is None:
        raise SystemExit("Missing overall_score.score")
    score_i = int(score_value)
    if score_i < 1 or score_i > 10:
        raise SystemExit("overall_score.score must be 1-10")

    score_wrapper = ScoreWrapper(score_i, severity or "Medium")
    emoji = _emoji_for(score_wrapper.severity)

    security_review = model.get("security_review") or {}
    recommendations_block = _recommendations(security_review.get("recommendations"), score_i)
    countermeasures_block = _bullets(_as_list(security_review.get("countermeasures")))
    assumptions_block = _bullets(_as_list(security_review.get("assumptions")))
    key_evidence = _as_list(security_review.get("key_evidence"))
    key_evidence_block = _bullets(security_review.get("key_evidence_deep") or key_evidence or ["TODO"])

    template_text = template_path.read_text(encoding="utf-8", errors="replace")
    render_text = _extract_template_body(template_text)

    env = Environment(loader=BaseLoader(), keep_trailing_newline=True, undefined=StrictUndefined)
    template = env.from_string(render_text)

    context = _build_render_context(model, score_wrapper, emoji)
    context.update(
        {
            "recommendations_checkboxes": recommendations_block,
            "countermeasures_bullets": countermeasures_block,
            "assumptions_bullets": assumptions_block,
            "key_evidence_bullets": key_evidence_block,
            "security_review_summary": security_review.get("summary", ""),
            "applicability_evidence": security_review.get("applicability", {}).get("evidence", ""),
            "exploitability": security_review.get("exploitability", "TODO"),
            "rationale": security_review.get("rationale", "TODO"),
            "security_review": security_review,
            "overview_bullets": model.get("overview_bullets") or [],
            "title": title,
            "description": str(model.get("description", "")).strip() or title,
            "rule_id": model.get("rule_id"),
            "source_file": model.get("source_file"),
            "source_line": model.get("source_line"),
            "language": model.get("language"),
            "framework": model.get("framework"),
        }
    )

    output = template.render(**context)
    return output.rstrip() + "\n"

def get_finding_model_from_db(finding_id: str) -> dict:
    """Get finding model from the Cozo DB."""
    try:
        row, contexts = get_finding_with_context(finding_id)
    except KeyError:
        raise ValueError(f"Finding not found in Cozo DB: {finding_id}")

    metadata = _parse_metadata(row.get("metadata_json"))
    provider_raw = metadata.get("provider") or row.get("provider") or "unknown"
    provider = str(provider_raw).strip() or "unknown"
    provider_display = provider.capitalize()

    severity_label, fallback_score = severity_summary(row.get("severity"))
    raw_score = row.get("severity_score")
    try:
        score_i = clamp_score(int(raw_score)) if raw_score is not None else clamp_score(fallback_score)
    except (TypeError, ValueError):
        score_i = clamp_score(fallback_score)

    rule_id = str(row.get("rule_id") or row.get("check_id") or "finding").strip()
    title = metadata.get("title") or _humanize_rule_id(rule_id)
    description = str(row.get("message") or metadata.get("description") or metadata.get("summary") or title).strip() or title

    source_file = row.get("source_file") or "unknown"
    start_line = row.get("start_line") or 0
    location = f"{source_file}:{start_line}"
    repo_name = str(row.get("repo_name") or "unknown_repo")
    category = metadata.get("category") or row.get("category") or "Unknown"

    architecture = metadata.get("architecture_mermaid") or f"flowchart LR\n  {provider_display} --> {rule_id}"
    context_lines = _format_context_lines(contexts)
    key_evidence = context_lines or [f"Detected by {rule_id}"]

    security_review = {
        "summary": description,
        "applicability": {
            "status": "Yes",
            "evidence": f"{rule_id} at {location}",
        },
        "key_evidence": key_evidence,
        "key_evidence_deep": context_lines or key_evidence,
        "assumptions": _as_list(metadata.get("assumptions")),
        "exploitability": metadata.get("exploitability") or "Not assessed",
        "recommendations": _normalize_recommendations(metadata.get("recommendations"), score_i),
        "countermeasures": _as_list(metadata.get("countermeasures")),
        "risks": _as_list(metadata.get("risks")),
        "rationale": metadata.get("rationale") or description,
    }

    overview_bullets = [
        f"Repo: {repo_name}",
        f"Location: {location}",
        f"Provider: {provider_display}",
    ]
    if description:
        overview_bullets.append(f"Details: {description}")

    language = metadata.get("language") or metadata.get("languages") or "plaintext"
    framework = metadata.get("framework") or metadata.get("technology") or "Unknown"
    collaboration = metadata.get("collaboration") or {
        "outcome": "Pending collaboration",
        "next_step": "TBD",
    }
    compounding_findings = metadata.get("compounding_findings") or []
    entry_point = metadata.get("entry_point") or metadata.get("route") or "Entry point TBD"
    vulnerable_component = metadata.get("vulnerable_component") or "Vulnerable component TBD"
    data_store = metadata.get("data_store") or "Data store TBD"
    code_snippet = metadata.get("code_snippet") or ""
    vulnerable_snippet = metadata.get("vulnerable_snippet") or ""
    fixed_snippet = metadata.get("fixed_snippet") or ""

    meta = {
        "category": category,
        "languages": metadata.get("languages") or "Unknown",
        "source": metadata.get("source") or "Cozo scan",
        "validation_status": metadata.get("validation_status") or "⚠️ Draft - Needs Triage",
        "last_updated": metadata.get("last_updated") or datetime.now().strftime("%d/%m/%Y %H:%M"),
        "repo_name": repo_name,
        "provider": provider_display,
    }

    return {
        "version": 1,
        "kind": "cloud" if provider.lower() in {"azure", "aws", "gcp", "alibaba", "oracle"} else "code",
        "title": title,
        "description": description,
        "overall_score": {
            "score": score_i,
            "severity": severity_label,
        },
        "architecture_mermaid": architecture,
        "security_review": security_review,
        "overview_bullets": overview_bullets,
        "meta": meta,
        "provider": provider_display,
        "resource_type": metadata.get("resource_type") or category,
        "rule_id": rule_id,
        "repo_name": repo_name,
        "source_file": source_file,
        "source_line": start_line,
        "language": language,
        "framework": framework,
        "entry_point": entry_point,
        "vulnerable_component": vulnerable_component,
        "data_store": data_store,
        "route_line": metadata.get("route_line") or start_line,
        "auth_file": metadata.get("auth_file") or source_file,
        "auth_line": metadata.get("auth_line") or start_line,
        "code_snippet": code_snippet,
        "vulnerable_snippet": vulnerable_snippet,
        "fixed_snippet": fixed_snippet,
        "compounding_findings": compounding_findings,
        "collaboration": collaboration,
    }

def compute_output_path(model: dict) -> Path:
    out = (model.get("output") or {}).get("path")
    if out:
        p = Path(str(out))
        if not p.is_absolute():
            return (ROOT / p).resolve()
        return p

    kind = str(model.get("kind", "")).strip().lower()
    title = str(model.get("title", "")).strip()
    repo_name = str((model.get("meta") or {}).get("repo_name", "unknown_repo")).strip()

    sub = "Cloud" if kind == "cloud" else ("Code" if kind == "code" else ("IaC" if kind == "iac" else ("Secrets" if kind == "secrets" else "Repo")))
    return (OUTPUT_FINDINGS_DIR / sub / repo_name / _safe_filename(title)).resolve()


def main() -> int:
    p = argparse.ArgumentParser(description="Render a finding Markdown file from the Cozo DB.")
    p.add_argument("--id", dest="finding_id", type=str, required=True, help="Cozo finding_id (hash) to render.")
    p.add_argument("--out", dest="out_path", help="Override output Markdown path")
    args = p.parse_args()

    try:
        model = get_finding_model_from_db(args.finding_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    md = render_md(model)

    out_path = Path(args.out_path).expanduser() if args.out_path else compute_output_path(model)
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
