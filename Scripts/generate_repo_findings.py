#!/usr/bin/env python3
"""Generate Repo finding Markdown files from local repo scans.

This script is intended to turn a quick, stdlib-only scan (Scripts/scan_repo_quick.py)
into well-formed repo findings under Output/Findings/Repo/.

Writes (by default)
- Output/Findings/Repo/Repo_<RepoFolderName>.md
- Output/Audit/RepoScans.md (append-only)
- Output/Knowledge/<Provider>.md (optional, minimal updates)

Usage examples
  # Single repo
  python3 Scripts/generate_repo_findings.py /abs/path/to/repo

  # Expand a simple pattern under a repo root
  python3 Scripts/generate_repo_findings.py --repos-root /mnt/c/Repos --pattern 'terraform-*' --update-knowledge
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from output_paths import OUTPUT_AUDIT_DIR, OUTPUT_FINDINGS_DIR, OUTPUT_KNOWLEDGE_DIR, OUTPUT_RENDER_INPUTS_DIR


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


def severity(score: int) -> tuple[str, str]:
    if score >= 8:
        return "🔴", "Critical"
    if score >= 6:
        return "🟠", "High"
    if score >= 4:
        return "🟡", "Medium"
    return "🟢", "Low"


@dataclass
class Scan:
    repo_path: Path
    key_files: list[str]
    tf_hits: list[str]
    secret_hits: list[str]


def _extract_section(lines: list[str], header: str, limit: int) -> list[str]:
    out: list[str] = []
    in_sec = False
    for l in lines:
        if l.strip() == header:
            in_sec = True
            continue
        if in_sec and l.startswith("== "):
            break
        if in_sec:
            s = l.strip()
            if not s:
                continue
            out.append(s)
            if len(out) >= limit:
                break
    return out


def run_quick_scan(repo: Path) -> Scan:
    cmd = [sys.executable, str(ROOT / "Skills" / "scan_repo_quick.py"), str(repo.resolve())]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"scan_repo_quick.py failed for {repo}:\n{p.stderr.strip()}")

    lines = p.stdout.splitlines()

    key_files = _extract_section(lines, "== Key files (top 80) ==", limit=80)
    tf_hits = _extract_section(lines, "== Terraform module/provider usage (first 120 matches) ==", limit=120)
    secret_hits = _extract_section(lines, "== Potential secrets (first 120 matches) ==", limit=120)

    return Scan(repo_path=repo.resolve(), key_files=key_files, tf_hits=tf_hits, secret_hits=secret_hits)


def detect_ci(key_files: list[str], secret_hits: list[str]) -> str:
    joined = "\n".join(key_files + secret_hits).lower()
    if "azure-pipelines" in joined or ".azuredevops/" in joined:
        return "Azure Pipelines"
    if ".github/workflows" in joined:
        return "GitHub Actions"
    if "gitlab-ci" in joined:
        return "GitLab CI"
    return "Unknown"


def detect_langs(key_files: list[str]) -> list[str]:
    langs: list[str] = []
    if any(k.endswith(".tf") or "/.tf" in k for k in key_files) or any(".tf" in k for k in key_files):
        langs.append("Terraform")
    if any(k.endswith("go.mod") for k in key_files):
        langs.append("Go")
    if any(k.endswith("package.json") for k in key_files):
        langs.append("Node.js")
    if any(k.endswith(".csproj") for k in key_files):
        langs.append(".NET")
    if any(k.endswith("Dockerfile") for k in key_files):
        langs.append("Containers")
    if any("hiera/" in k for k in key_files):
        langs.append("Hiera/YAML")
    return langs or ["Unknown"]


def detect_provider(tf_hits: list[str]) -> str:
    joined = "\n".join(tf_hits).lower()
    if 'provider "azurerm"' in joined or "azurerm" in joined:
        return "Azure"
    if 'provider "aws"' in joined or "aws" in joined:
        return "AWS"
    if 'provider "google"' in joined or "google" in joined or "gcp" in joined:
        return "GCP"
    return "Unknown"


def score_repo(secret_hits: list[str]) -> int:
    blob = "\n".join(secret_hits).lower()

    # Conservative heuristics; intended to be refined later with context.
    score = 5

    if any(k in blob for k in ["azuread_application_password", "client_secret", "serviceprincipalkey"]):
        score = 7
    elif any(k in blob for k in ["connection_string", "connection_strings", "access_key"]):
        score = 6

    # Positive signal (not definitive): secret scanning present.
    if "ggshield secret scan" in blob and score > 4:
        score -= 1

    return score


def _display_repo_name(repo_folder: str) -> str:
    # Titles must not include underscores.
    return repo_folder.replace("_", "-")


def _pick_evidence(secret_hits: list[str], n: int = 3) -> list[str]:
    # Prefer IaC/pipeline lines over docs where possible.
    pri: list[str] = []
    for l in secret_hits:
        ll = l.lower()
        if any(x in ll for x in [".tf:", ".yaml:", ".yml:", ".ps1:", ".sh:", ".json:"]):
            pri.append(l)
    if not pri:
        pri = secret_hits

    picked: list[str] = []
    for key in ["azuread_application_password", "serviceprincipalkey", "client_secret", "connection_string", "access_key", "system.accesstoken"]:
        for l in pri:
            if key in l.lower() and l not in picked:
                picked.append(l)
                if len(picked) >= n:
                    return picked

    for l in pri:
        if l not in picked:
            picked.append(l)
            if len(picked) >= n:
                break
    return picked


def infer_services_from_repo_name(repo_folder: str) -> list[str]:
    # Minimal inference for Azure Terraform repos.
    name = repo_folder.lower()
    out: list[str] = []
    if "acr" in name:
        out.append("Azure Container Registry (ACR)")
    if "aks" in name:
        out.append("Azure Kubernetes Service (AKS)")
    if "key_vault" in name or "key-vault" in name:
        out.append("Key Vault")
    if "cosmos" in name:
        out.append("Cosmos DB")
    if "service_bus" in name or "service-bus" in name:
        out.append("Service Bus")
    if "blob" in name or "storage" in name:
        out.append("Storage Account")
    if "app_gateway" in name or "app-gateway" in name:
        out.append("Application Gateway")
    if "app_service" in name or "app-service" in name:
        out.append("App Service")
    if "firewall" in name:
        out.append("Azure Firewall")
    if "ddos" in name:
        out.append("DDoS Protection")
    if "network" in name:
        out.append("VNet/NSG/Private Endpoints")
    if "service_fabric" in name or "service-fabric" in name:
        out.append("Service Fabric")
    return out


def ensure_knowledge_file(provider: str) -> Path:
    path = OUTPUT_KNOWLEDGE_DIR / f"{provider}.md"
    if path.exists():
        return path

    OUTPUT_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {provider}\n\n"
        "## Confirmed\n\n"
        "## Assumptions\n\n"
        "## Unknowns\n\n",
        encoding="utf-8",
    )
    return path


def append_unique_bullet(path: Path, section: str, bullet: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if bullet in text:
        return

    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for i, l in enumerate(lines):
        out.append(l)
        if l.strip() == section:
            # Insert after the section heading (and any immediate blank line).
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            # Only insert once.
            if not inserted:
                out.append(f"- {bullet}")
                inserted = True
    if not inserted:
        # Fallback: append to end.
        out.append(section)
        out.append(f"- {bullet}")

    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def write_audit(repos: list[Path], scope: str) -> Path:
    OUTPUT_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_AUDIT_DIR / "RepoScans.md"
    header = "# Repo scans\n"
    if not path.exists():
        path.write_text(header, encoding="utf-8")

    ts = now_uk()
    body = (
        f"\n## {ts} — Repo scan\n"
        f"- **Scope:** {scope}\n"
        f"- **Repos:** " + ", ".join(str(p) for p in repos) + "\n"
    )

    path.write_text(path.read_text(encoding="utf-8", errors="replace").rstrip() + body, encoding="utf-8")
    return path


def write_finding(scan: Scan, *, scope: str, update_knowledge: bool) -> Path:
    repo_folder = scan.repo_path.name
    title_repo = _display_repo_name(repo_folder)

    ci = detect_ci(scan.key_files, scan.secret_hits)
    langs = detect_langs(scan.key_files)
    provider = detect_provider(scan.tf_hits)
    score = score_repo(scan.secret_hits)
    emoji, label = severity(score)
    services = infer_services_from_repo_name(repo_folder)

    finding_dir = OUTPUT_FINDINGS_DIR / "Repo"
    finding_dir.mkdir(parents=True, exist_ok=True)

    out_path = finding_dir / f"Repo_{repo_folder}.md"

    ev_tf = scan.tf_hits[0] if scan.tf_hits else ""
    ev_secrets = _pick_evidence(scan.secret_hits, n=3)

    md: list[str] = []
    md.append(f"# 🟣 Repo {title_repo}")
    md.append("")
    md.append("## 🗺️ Architecture Diagram")
    md.append("```mermaid")
    md.append("graph TB")
    md.append(f"  Dev[{repo_folder} repo] --> CI[{ci}];")
    md.append("  CI --> TF[Terraform plan/apply];")
    md.append(f"  TF --> Cloud[{provider} resources];")
    if services:
        svc_label = ", ".join(services).replace('"', "\\\"")
        md.append(f"  Cloud --> Svc[\"{svc_label}\"]; ")
    md.append("  CI --> Secrets[Secret scanning];")
    md.append("```")
    md.append("")

    md.append("- **Description:** Repo scan summary (IaC + secrets + dependency/module discovery).")
    md.append(f"- **Overall Score:** {emoji} {label} {score}/10")
    md.append("")

    md.append("## 🧭 Overview")
    md.append(f"- **Repo path:** {scan.repo_path}")
    md.append("- **Repo URL (if applicable):** N/A")
    md.append(f"- **Scan scope:** {scope}")
    md.append(f"- **Languages/frameworks detected:** {', '.join(langs)}")
    if scan.key_files:
        md.append("- **Evidence for detection:** " + ", ".join(f"`{k.replace('./','')}`" for k in scan.key_files[:6]))
    md.append(f"- **CI/CD:** {ci}")
    md.append("")

    md.append("## 🛡️ Security Review")
    md.append("### Languages & Frameworks (extracted)")
    for l in langs:
        md.append(f"- {l} — evidence: `{repo_folder}`")
    md.append("")

    md.append("### 🧾 Summary")
    md.append(
        "This repo appears to manage cloud infrastructure via IaC. Secret-like values (tokens, passwords, "
        "connection strings) may be referenced in code/config and can leak via Terraform state, CI variables, "
        "or outputs if not handled as sensitive."
    )
    md.append("")

    md.append("### ✅ Applicability")
    md.append("- **Status:** Yes")
    md.append("- **Evidence:** IaC files and provider/module usage were detected in the repo.")
    md.append("")

    md.append("### ⚠️ Assumptions")
    md.append("- Terraform state is stored in a remote backend with encryption and restricted read access (Unconfirmed).")
    md.append("- Secrets are injected at runtime (CI secret vars / secret store) rather than committed (Unconfirmed).")
    md.append("")

    md.append("### 🎯 Exploitability")
    md.append(
        "If an attacker (or over-privileged insider) can read Terraform state files, CI variable groups, "
        "or pipeline logs, they may recover credentials/connection strings and pivot into the cloud environment."
    )
    md.append("")

    md.append("### 🚩 Risks")
    md.append("- State/outputs/logs may expose secret material if not treated as sensitive.")
    md.append("- Long-lived credentials in automation increase blast radius if leaked.")
    md.append("")

    md.append("### 🔎 Key Evidence (deep dive)")
    if ev_tf:
        md.append(f"- ✅ IaC provider/module usage — evidence: `{ev_tf.lstrip('./')}`")
    for l in ev_secrets:
        md.append(f"- ❌ Secret-like signal — evidence: `{l.lstrip('./')}`")
    md.append("")

    md.append("### Cloud Environment Implications")
    md.append(f"- **Provider(s) referenced:** {provider}")
    if services:
        md.append("- **Cloud resources/services deployed or referenced:** " + ", ".join(services))
    md.append("- **Network posture patterns:** Unconfirmed (requires deeper review).")
    md.append("- **Identity patterns:** Unconfirmed (requires deeper review).")
    md.append("- **Guardrails:** Unconfirmed (requires deeper review).")
    md.append("")

    md.append("### Service Dependencies (from config / connection strings)")
    md.append("- **Datastores:** N/A")
    md.append("- **Messaging:** N/A")
    md.append("- **Logging/Monitoring:** N/A")
    md.append("- **External APIs:** N/A")
    md.append("")

    md.append("### Containers / Kubernetes (inferred)")
    md.append("- **Kubernetes tooling found:** N/A")
    md.append("- **Container build artifacts:** N/A")
    md.append("- **Base images:** N/A")
    md.append("")

    md.append("### ✅ Recommendations")
    md.append("- [ ] Treat Terraform state as secret (encrypt + least-privilege access + audit) — ⬇️ 7➡️4 (est.)")
    md.append("- [ ] Mark secret-like outputs as `sensitive = true` and minimise outputs — ⬇️ 7➡️5 (est.)")
    md.append("- [ ] Prefer managed identity / workload identity federation over long-lived client secrets — ⬇️ 7➡️4 (est.)")
    md.append("- [ ] Standardise CI secret scanning + block merges on findings — ⬇️ 7➡️5 (est.)")
    md.append("")

    md.append("### 📐 Rationale")
    md.append("Score is driven by secret-like material detected in quick scan results; severity depends on state/CI access controls.")
    md.append("")

    md.append("## 🤔 Skeptic")
    md.append("> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.")
    md.append("")
    md.append("### 🛠️ Dev")
    md.append("- **What’s missing/wrong vs Security Review:** quick scan may surface docs/examples; confirm whether any real secrets hit state/outputs.")
    md.append("- **Score recommendation:** ➡️ Keep — until backend + permissions are confirmed.")
    md.append("- **How it could be worse:** state stored locally or in broadly-readable storage; outputs copied into tickets/chats.")
    md.append("- **Countermeasure effectiveness:** state hardening + removing secret outputs measurably reduces blast radius.")
    md.append("- **Assumptions to validate:** state backend, state readers, secret rotation.")
    md.append("")
    md.append("### 🏗️ Platform")
    md.append("- **What’s missing/wrong vs Security Review:** validate guardrails enforce private networking/RBAC baselines across environments.")
    md.append("- **Score recommendation:** ⬆️ Up if state/CI variable access is broad across teams/environments.")
    md.append("- **Operational constraints:** moving to federation may require pipeline/service-connection changes.")
    md.append("- **Countermeasure effectiveness:** shared modules + CI checks help only if adoption/enforcement is real.")
    md.append("- **Assumptions to validate:** Azure DevOps permissions (variable groups, service connections).")
    md.append("")

    md.append("## 🤝 Collaboration")
    md.append("- **Outcome:** Repo scan completed; evidence pointers captured.")
    md.append("- **Next step:** Confirm state backend + secret-handling patterns to refine scoring.")
    md.append("")

    md.append("## Compounding Findings")
    md.append("- **Compounds with:** None identified")
    md.append("")

    md.append("## Meta Data")
    md.append("<!-- Meta Data must remain the final section in the file. -->")
    md.append(f"- 🗓️ **Last updated:** {now_uk()}")

    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    if update_knowledge and provider in {"Azure", "AWS", "GCP"}:
        k = ensure_knowledge_file(provider)
        append_unique_bullet(k, "## Confirmed", f"{now_uk()} — Repo scan found IaC usage in `{repo_folder}`.")
        if ci != "Unknown":
            append_unique_bullet(k, "## Confirmed", f"{now_uk()} — CI/CD signal observed: {ci} (repo `{repo_folder}`).")
        for s in services:
            append_unique_bullet(k, "## Confirmed", f"{now_uk()} — Service appears in IaC repo set: {s} (from repo name `{repo_folder}`).")

    return out_path


def emit_render_json(out_path: Path, scan: Scan, *, scope: str) -> Path:
    """Write a JSON finding-model next to other render inputs (audit/debug only)."""

    # Minimal model that the template-driven renderer can use.
    repo_folder = scan.repo_path.name
    title_repo = _display_repo_name(repo_folder)

    ci = detect_ci(scan.key_files, scan.secret_hits)
    langs = detect_langs(scan.key_files)
    provider = detect_provider(scan.tf_hits)
    score = score_repo(scan.secret_hits)
    emoji, label = severity(score)
    services = infer_services_from_repo_name(repo_folder)

    overview: list[str] = [
        f"**Repo path:** `{scan.repo_path}`",
        f"**Scan scope:** {scope}",
        f"**Languages/frameworks detected:** {', '.join(langs)}",
        f"**CI/CD:** {ci}",
        f"**Provider(s) referenced:** {provider}",
    ]
    if services:
        overview.append("**Cloud resources/services referenced:** " + ", ".join(services))

    # Evidence lines are already preformatted as strings in scan_* outputs; keep them simple.
    key_evidence_deep: list[str] = []
    if scan.tf_hits:
        key_evidence_deep.append(f"✅ IaC provider/module usage — evidence: `{scan.tf_hits[0].lstrip('./')}`")
    for l in _pick_evidence(scan.secret_hits, n=5):
        key_evidence_deep.append(f"❌ Secret-like signal — evidence: `{l.lstrip('./')}`")

    model: dict = {
        "version": 1,
        "kind": "repo",
        "title": f"Repo {title_repo}",
        "description": "Repo scan summary (IaC + secrets + dependency/module discovery).",
        "overall_score": {"severity": label, "score": score},
        "architecture_mermaid": "graph TB\n  Dev[Repo] --> CI[CI/CD];\n  CI --> TF[Terraform plan/apply];\n  TF --> Cloud[Cloud resources];\n  CI --> Secrets[Secret scanning];",
        "overview_bullets": overview,
        "security_review": {
            "summary": (
                "This repo appears to manage cloud infrastructure via IaC. Secret-like values (tokens, passwords, "
                "connection strings) may be referenced in code/config and can leak via Terraform state, CI variables, "
                "or outputs if not handled as sensitive."
            ),
            "applicability": {"status": "Yes", "evidence": "IaC files and provider/module usage were detected in the repo."},
            "assumptions": [
                "Terraform state is stored in a remote backend with encryption and restricted read access (Unconfirmed).",
                "Secrets are injected at runtime (CI secret vars / secret store) rather than committed (Unconfirmed).",
            ],
            "exploitability": (
                "If an attacker (or over-privileged insider) can read Terraform state files, CI variable groups, "
                "or pipeline logs, they may recover credentials/connection strings and pivot into the cloud environment."
            ),
            "risks": [
                "State/outputs/logs may expose secret material if not treated as sensitive.",
                "Long-lived credentials in automation increase blast radius if leaked.",
            ],
            "key_evidence_deep": key_evidence_deep,
            "recommendations": [
                {"text": "Confirm Terraform state backend, encryption, and who can read state; remove secret outputs", "score_from": score, "score_to": max(score - 2, 0)},
                {"text": "Move CI/CD to federated auth (OIDC/workload identity) and rotate any long-lived credentials", "score_from": max(score - 2, 0), "score_to": max(score - 4, 0)},
            ],
            "countermeasures": [
                "🟡 Secret scanning — helpful, but doesn't prevent secrets in state/outputs.",
                "🟢 Harden state backend + least privilege — directly reduces blast radius and exposure.",
            ],
            "rationale": "Score is driven by secret-like material detected in quick scan results; severity depends on state/CI access controls.",
        },
        "meta": {
            "category": "Repo scan (draft)",
            "languages": ", ".join(langs),
            "source": "Scripts/generate_repo_findings.py",
            "validation_status": "⚠️ Draft - Needs Triage",
            "last_updated": now_uk(),
        },
        "output": {"path": str(out_path.relative_to(ROOT))},
    }

    out_dir = OUTPUT_RENDER_INPUTS_DIR / "Repo"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / out_path.with_suffix(".json").name
    json_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return json_path


def expand_pattern(repos_root: Path, pattern: str) -> list[Path]:
    if not repos_root.is_dir():
        raise SystemExit(f"repos root not found: {repos_root}")

    out: list[Path] = []
    for name in sorted(p.name for p in repos_root.iterdir() if p.is_dir()):
        if fnmatch.fnmatch(name, pattern):
            out.append(repos_root / name)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Generate Output/Findings/Repo markdown from local repo scans")
    p.add_argument("repos", nargs="*", help="Repo paths to scan")
    p.add_argument("--repos-root", help="Root directory containing repos (for --pattern expansion)")
    p.add_argument("--pattern", help="Simple pattern to expand under --repos-root (e.g., terraform-*)")
    p.add_argument("--scope", default="All (IaC + secrets + dependency/module discovery)")
    p.add_argument("--update-knowledge", action="store_true", help="Also update Output/Knowledge/<Provider>.md")
    p.add_argument(
        "--emit-render-json",
        action="store_true",
        help="Also write JSON render inputs under Output/Audit/RenderInputs/Repo/ (repro/debug only).",
    )
    p.add_argument("--no-audit", action="store_true", help="Do not append Output/Audit/RepoScans.md")
    args = p.parse_args()

    repos: list[Path] = []
    repos.extend(Path(r).expanduser() for r in args.repos)

    if args.pattern:
        if not args.repos_root:
            raise SystemExit("--pattern requires --repos-root")
        repos.extend(expand_pattern(Path(args.repos_root).expanduser(), args.pattern))

    # Normalise to absolute paths and de-dupe.
    uniq: dict[str, Path] = {}
    for r in repos:
        rr = r
        if not rr.is_absolute():
            rr = (ROOT / rr).resolve()
        uniq[str(rr)] = rr

    repos = list(uniq.values())
    if not repos:
        raise SystemExit("No repos provided. Provide paths or use --repos-root + --pattern.")

    missing = [r for r in repos if not r.is_dir()]
    if missing:
        raise SystemExit("Repo path(s) not found: " + ", ".join(str(m) for m in missing))

    written: list[Path] = []
    for repo in repos:
        scan = run_quick_scan(repo)
        out_path = write_finding(scan, scope=args.scope, update_knowledge=args.update_knowledge)
        written.append(out_path)
        if args.emit_render_json:
            emit_render_json(out_path, scan, scope=args.scope)

    if not args.no_audit:
        audit = write_audit(repos, args.scope)
        print(f"Audit updated: {audit}")

    # Validate and auto-fix Mermaid blocks in generated markdown.
    try:
        from markdown_validator import validate_markdown_file

        fixed = 0
        for p in written:
            before = p.read_text(encoding="utf-8", errors="replace")
            probs = validate_markdown_file(p, fix=True)
            after = p.read_text(encoding="utf-8", errors="replace")
            if after != before:
                fixed += 1
            for pr in probs:
                if pr.level == "ERROR":
                    print(f"ERROR: {p.relative_to(ROOT)}:{pr.line or ''} - {pr.message}")
                    return 1
        if fixed:
            print(f"Auto-fixed Mermaid blocks in {fixed} generated file(s)")
    except Exception as e:
        print(f"WARN: Mermaid validation skipped: {e}")

    print(f"Wrote {len(written)} finding(s) under {OUTPUT_FINDINGS_DIR / 'Repo'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
