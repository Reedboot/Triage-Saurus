#!/usr/bin/env python3
"""Generate draft findings from title-only inputs.

Use this when you have many findings that are only a short title (e.g., from a
scanner export) and you want to quickly create well-formed finding Markdown
files that match the repository templates.

Inputs
- A folder containing files (recursively):
  - 1 file per finding: uses the first non-empty line as the title.
  - `.txt` / `.csv`: treated as 1 finding per line (entire line is the title).

Outputs
- Writes finding files to an output folder you specify.
- Optionally updates Knowledge/<Provider>.md and Summary/Cloud/Architecture_<Provider>.md.
- When `--update-knowledge` is used, also generates per-service summary files under `Summary/Cloud/`
  and regenerates the executive risk register (`Summary/Risk Register.xlsx`).

Example
  python3 Skills/generate_findings_from_titles.py \
    --provider azure \
    --in-dir "Intake/Cloud" \
    --out-dir "Findings/Cloud" \
    --update-knowledge
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from output_paths import (
    OUTPUT_AUDIT_DIR,
    OUTPUT_FINDINGS_DIR,
    OUTPUT_KNOWLEDGE_DIR,
    OUTPUT_SUMMARY_DIR,
)

from markdown_validator import validate_markdown_file


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


def _normalise_title(line: str) -> str:
    # Accept both raw lines and Markdown headings.
    return line.strip().lstrip("\ufeff").lstrip("# ").strip()


def _dedupe_key(title: str) -> str:
    # Coarse dedupe for bulk imports: avoid generating multiple findings for the same title.
    # Keep it conservative: whitespace/case normalisation + common /etc/*- backup patterns.
    s = _normalise_title(title).lower()
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    s = re.sub(r"(/etc/(?:shadow|gshadow|passwd|group))\-(?=\s)", r"\1", s)
    return s


def _titles_from_path(path: Path) -> list[str]:
    """Extract one or more finding titles from an input path."""

    ext = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")

    if ext in {".txt", ".csv"}:
        return [t for t in (_normalise_title(l) for l in text.splitlines()) if t]

    # Default: 1 file = 1 finding (first non-empty line).
    for line in text.splitlines():
        t = _normalise_title(line)
        if t:
            return [t]
    return []


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


def titlecase_filename(title: str) -> str:
    # Keep filenames predictable and safe.
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    # Titlecase convention: initial caps per segment, no underscores in title rules
    # are for document titles, not filenames; filenames can keep underscores.
    parts = [p for p in cleaned.split("_") if p]
    return "_".join([p[:1].upper() + p[1:] for p in parts]) or "Finding"


def score_for(title: str) -> int:
    t = title.lower()
    # Heuristic scoring only; user should validate.
    if any(k in t for k in ["exposed management", "management ports", "rdp", "ssh from the internet"]):
        return 9
    if any(k in t for k in ["unrestricted", "allow broad access", "0.0.0.0", "any any"]):
        return 7
    if any(k in t for k in ["public access", "public blob", "public network"]):
        return 7
    if any(k in t for k in ["rbac disabled", "disable admin", "shared key", "allow azure services"]):
        return 6
    if any(k in t for k in ["ddos", "auditing", "endpoint protection", "disk encryption"]):
        return 5
    if any(k in t for k in ["expiration date", "secure transfer", "ftps"]):
        return 4
    return 5


def recommendations_for(title: str) -> list[str]:
    t = title.lower()
    if "mfa" in t:
        return [
            "Require MFA for privileged roles (owners/admins) via conditional access",
            "Use just-in-time elevation (e.g., PIM) for subscription/project admin roles",
        ]
    if "managed identity" in t:
        return [
            "Use managed identities / workload identity instead of stored secrets",
            "Rotate and remove existing secrets from code, CI/CD variables, and config",
        ]
    if any(k in t for k in ["nsg", "security group", "firewall rule", "management ports", "unrestricted"]):
        return [
            "Remove broad inbound rules; restrict sources to approved IP ranges and only required ports",
            "Use bastion/jump hosts or just-in-time access for administration instead of direct inbound",
        ]
    if any(k in t for k in ["key vault", "secrets", "kms"]):
        return [
            "Restrict secrets-store access (private endpoints/firewall) and disable public network access",
            "Enforce least privilege (RBAC) and implement rotation/expiry for keys and secrets",
        ]
    if any(k in t for k in ["storage", "s3", "blob"]):
        return [
            "Disable public access unless explicitly required and restrict network access",
            "Prefer identity-based access; minimise shared keys and long-lived tokens",
        ]
    if any(k in t for k in ["sql", "database", "tde", "encryption"]):
        return [
            "Enable encryption at rest and validate key management/rotation",
            "Enable auditing/logging and alert on suspicious or privileged actions",
        ]
    return [
        "Apply the recommended secure configuration and enforce it with policy-as-code",
        "Add monitoring/alerting to detect drift and verify changes in lower environments first",
    ]


def write_finding(out_path: Path, title: str, score: int, ts: str) -> None:
    sev = severity(score)
    recs = recommendations_for(title)

    reduced_1 = max(0, score - 2)
    reduced_2 = max(0, reduced_1 - 2)

    out_path.write_text(
        f"""# 🟣 {title}

## 🗺️ Architecture Diagram
```mermaid
flowchart TB
  Internet[Internet / Users] --> Svc[Affected service]
  Svc --> Data[Data store]
  Svc --> Logs[Monitoring/Logs]

  Sec[Controls] -.-> Svc
```

- **Description:** {title}
- **Overall Score:** {sev} {score}/10

## 🛡️ Security Review
### 🧾 Summary
This is a draft finding generated from a title-only input. Validate the affected
resources/scope and confirm whether the exposure is internet-facing and/or impacts
production workloads.

### ✅ Applicability
- **Status:** Don’t know
- **Evidence:** Title-only input; needs validation.

### 🔎 Key Evidence
- <add evidence here, e.g., resource IDs, query output, screenshots, or IaC paths>

### ⚠️ Assumptions
- Unconfirmed: Scope includes production workloads.
- Unconfirmed: Exposure is internet-facing (where relevant).

### 🎯 Exploitability
Misconfiguration findings are typically exploitable by either (a) external attackers
when exposure is public, or (b) internal threat actors / compromised identities when
permissions and network paths are overly broad.

### ✅ Recommendations
- [ ] {recs[0]} — ⬇️ {score}➡️{reduced_1} (est.)
- [ ] {recs[1]} — ⬇️ {reduced_1}➡️{reduced_2} (est.)

### 🧰 Considered Countermeasures
- 🔴 Rely on ad-hoc manual configuration — prone to drift and gaps.
- 🟡 Point-in-time remediation only — helps now but without policy, issues often return.
- 🟢 Enforce with policy-as-code + monitoring — reduces recurrence and improves coverage.

### 📐 Rationale
The recommendation reduces attack surface and/or blast radius and should align with
provider baseline guidance. Confirm exact control mappings in your environment.

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** <fill in>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **How it could be worse:** <fill in>
- **Countermeasure effectiveness:** <fill in>
- **Assumptions to validate:** <fill in>

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** <fill in>
- **Service constraints checked:** <fill in: SKU/tier, downtime, cost>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **Operational constraints:** <fill in>
- **Countermeasure effectiveness:** <fill in>
- **Assumptions to validate:** <fill in>

## 🤝 Collaboration
- **Outcome:** Draft finding created; requires environment validation.
- **Next step:** Identify affected resources and confirm true exposure.

## Compounding Findings
- **Compounds with:** None identified

## Meta Data
- 🗓️ **Last updated:** {ts}
""",
        encoding="utf-8",
    )

    probs = validate_markdown_file(out_path, fix=True)
    errs = [p for p in probs if p.level == "ERROR"]
    if errs:
        raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")


def ensure_knowledge(provider: str, ts: str) -> Path:
    path = OUTPUT_KNOWLEDGE_DIR / f"{provider.title()}.md"
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# {provider.title()} Knowledge (Confirmed + Assumptions)

## Confirmed
- [{ts}] Provider selected for triage: {provider.title()}.

## Assumptions
- (none yet)

## Audit log (not knowledge)
- **Audit folder:** `Audit/` (do not load into triage context)
""",
        encoding="utf-8",
    )
    return path


def append_audit_event(audit_path: Path, entry_lines: list[str]) -> None:
    existing = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    block = "\n".join(entry_lines).rstrip() + "\n"
    if block in existing:
        return
    if not existing:
        audit_path.write_text(block, encoding="utf-8")
        return
    audit_path.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")


def ensure_audit(provider: str) -> Path:
    audit_dir = OUTPUT_AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"KnowledgeImports_{provider.title()}.md"

    if audit_path.exists():
        return audit_path

    audit_path.write_text(
        "\n".join(
            [
                "<!--",
                "AUDIT LOG ONLY — do not load this file into LLM triage context.",
                "This is an append-only audit trail of bulk imports and automation events.",
                "-->",
                "",
                f"# 🟣 Audit Log — {provider.title()} Knowledge Imports",
                "",
                "## Purpose",
                "This file exists for auditing only. It should **not** be treated as environment knowledge.",
                "",
                "## Entries",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return audit_path


def update_knowledge_generic(knowledge_path: Path, provider: str, titles: list[str], ts: str) -> Path:
    """Write bulk-import audit events to Audit/, not Knowledge/."""

    audit_path = ensure_audit(provider)

    lines = [f"- [{ts}] Imported {len(titles)} title-only finding(s) via `Skills/generate_findings_from_titles.py`."
    ]
    for t in titles[:25]:
        lines.append(f"  - Finding imported (title-only): {t}.")
    if len(titles) > 25:
        lines.append("  - (truncated)")

    append_audit_event(audit_path, lines)
    return audit_path


def update_architecture(provider: str, ts: str) -> Path | None:
    if provider.lower() not in {"azure", "aws", "gcp"}:
        return None

    out = OUTPUT_SUMMARY_DIR / "Cloud" / f"Architecture_{provider.title()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Confirmed-only generic skeleton; specific services should be added during triage.
    out.write_text(
        f"""# 🟣 Architecture {provider.title()}

## 🧭 Overview
- **Provider:** {provider.title()}
- **Source:** Initial skeleton (confirmed provider selection only)
- **Last updated:** {ts}

```mermaid
flowchart TB
  Internet[Internet] --> Cloud[{provider.title()}]
```

## 📊 Service Risk Order
- 🔴 Public exposure (internet-facing endpoints)
- 🟠 Identity and privileged access
- 🟠 Secrets and data stores
- 🟡 Logging/monitoring and detection

## 📝 Notes
- Diagram includes confirmed items only.
- Add services as they become confirmed in Knowledge/.
""",
        encoding="utf-8",
    )

    probs = validate_markdown_file(out, fix=True)
    errs = [p for p in probs if p.level == "ERROR"]
    if errs:
        raise SystemExit(f"Mermaid validation failed for {out}: {errs[0].message}")

    return out


def _parse_overall_score(path: Path) -> tuple[str, str, int] | None:
    """Return (emoji, label, score) from a finding file."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("- **Overall Score:**"):
            tail = line.split("**Overall Score:**", 1)[-1].strip()
            m = re.search(r"^(🔴|🟠|🟡|🟢)\s+(Critical|High|Medium|Low)\s+(\d{1,2})/10", tail)
            if not m:
                return None
            return m.group(1), m.group(2), int(m.group(3))
    return None


def _parse_title(path: Path) -> str:
    first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    if not first:
        return path.stem
    return first[0].lstrip("# ").lstrip("🟣 ").strip()


def _link_text_for(path: Path) -> str:
    # Prefer filename-derived Titlecase (matches repo convention in Summary/).
    return path.stem.replace("_", " ")


def _rel_link_from_summary(rel_finding: str) -> str:
    # Summary/Cloud/*.md -> ../../Findings/Cloud/*.md
    return f"../../{rel_finding}"


def _summary_mermaid(service: str) -> str:
    # Keep Mermaid labels ASCII-only for broad renderer compatibility.
    if service == "Key Vault":
        return """```mermaid
flowchart TB
  User[Operator/Workload] --> Entra[Entra ID]
  Entra --> KV[Key Vault]
  App[App/Service] --> KV
  Internet[Internet] -. blocked by default .-> KV
```
"""
    if service == "Storage Accounts":
        return """```mermaid
flowchart TB
  App[App/Service] --> Storage[Storage Account]
  Entra[Entra ID] --> Storage
  Internet[Internet] -. public access (should be disabled) .-> Storage
```
"""
    if service in {"Azure SQL", "RDS", "Cloud SQL", "Databases"}:
        return """```mermaid
flowchart TB
  App[App/Service] --> DB[Database]
  Entra[Identity] --> DB
  Internet[Internet] -. blocked by firewall/private endpoint .-> DB
```
"""
    if service in {"AKS", "EKS", "GKE", "Kubernetes"}:
        return """```mermaid
flowchart TB
  Dev[Operator] --> IdP[Identity]
  IdP --> K8s[Kubernetes]
  K8s --> Registry[Image Registry]
  K8s --> Secrets[Secrets Store]
  K8s --> Storage[Storage]
```
"""
    if service in {"Container Registry", "ECR", "Artifact Registry", "Registry"}:
        return """```mermaid
flowchart TB
  CICD[CI/CD] --> IdP[Identity]
  IdP --> Registry[Image Registry]
  Runtime[Runtime (K8s/VM)] --> Registry
```
"""
    if service in {"App Service", "Lambda", "Cloud Run", "Compute"}:
        return """```mermaid
flowchart TB
  Internet[Internet] --> App[Service]
  CICD[CI/CD] --> App
  App --> Secrets[Secrets Store]
```
"""
    if service == "Virtual Machines":
        return """```mermaid
flowchart TB
  Internet[Internet] -. mgmt ports should be blocked .-> VM[Virtual Machines]
  Admin[Admin] --> Bastion[Bastion/JIT]
  Bastion --> VM
  VM --> Data[Data Stores]
```
"""
    if service == "Network":
        return """```mermaid
flowchart TB
  Internet[Internet] --> Edge[Public Edge]
  Edge --> NSG[Firewall/NSG]
  NSG --> Subnet[VPC/VNet]
  Subnet --> Workloads[Workloads]
```
"""
    if service == "Identity":
        return """```mermaid
flowchart TB
  User[Privileged user] --> MFA[MFA/Conditional Access]
  MFA --> IdP[Identity Provider]
  IdP --> Cloud[Cloud Control Plane]
```
"""
    return """```mermaid
flowchart TB
  Cloud[Cloud]
```
"""


def update_service_summaries(provider: str, ts: str) -> list[Path]:
    """Generate per-service summary Markdown under Summary/Cloud based on Findings/Cloud."""

    findings_dir = OUTPUT_FINDINGS_DIR / "Cloud"
    if not findings_dir.exists():
        return []

    # Provider-specific service buckets (keyword-based).
    provider_l = provider.lower()
    if provider_l == "azure":
        service_buckets = [
            ("Identity", ["mfa", "owner", "managed identity", "pim", "entra"]),
            ("Network", ["nsg", "network security group", "ports", "internet-facing", "ddos", "ip forwarding", "vnet", "virtual network"]),
            ("Virtual Machines", ["virtual machine", "vm", "endpoint protection", "disk encryption", "management ports"]),
            ("Key Vault", ["key vault", "keyvault", "secrets", "keys", "soft delete", "private link", "firewall"]),
            ("Storage Accounts", ["storage", "blob", "shared key", "secure transfer", "public access"]),
            ("Azure SQL", ["azure sql", "sql server", "sql databases", "transparent data encryption", "tde", "sql threat detection"]),
            ("PostgreSQL", ["postgres", "postgresql", "flexible server"]),
            ("AKS", ["kubernetes", "aks", "rbac"]),
            ("Container Registry", ["container registry", "acr", "admin user"]),
            ("App Service", ["app service", "ftps", "ftp"]),
        ]
    elif provider_l == "aws":
        service_buckets = [
            ("Identity", ["iam", "mfa", "access key", "assume role"]),
            ("Network", ["security group", "nacl", "vpc", "internet-facing", "ports", "0.0.0.0"]),
            ("Virtual Machines", ["ec2", "instance", "ssh", "rdp", "management ports"]),
            ("Secrets", ["secrets manager", "kms", "ssm parameter"]),
            ("Storage", ["s3", "bucket", "public access"]),
            ("RDS", ["rds", "database", "encryption", "audit"]),
            ("EKS", ["eks", "kubernetes", "rbac"]),
            ("ECR", ["ecr", "container registry"]),
            ("Lambda", ["lambda", "serverless"]),
        ]
    else:  # gcp
        service_buckets = [
            ("Identity", ["iam", "mfa", "service account"]),
            ("Network", ["vpc", "firewall", "internet-facing", "ports", "0.0.0.0"]),
            ("Virtual Machines", ["compute engine", "vm", "ssh", "rdp"]),
            ("Secret Manager", ["secret manager", "kms"]),
            ("Cloud Storage", ["cloud storage", "bucket", "public access"]),
            ("Cloud SQL", ["cloud sql", "database", "encryption", "audit"]),
            ("GKE", ["gke", "kubernetes", "rbac"]),
            ("Artifact Registry", ["artifact registry", "container registry"]),
            ("Cloud Run", ["cloud run", "serverless"]),
        ]

    buckets: dict[str, list[tuple[int, str, str, str]]] = {name: [] for name, _ in service_buckets}

    for path in sorted(findings_dir.glob("*.md")):
        title = _parse_title(path)
        parsed = _parse_overall_score(path)
        if not parsed:
            continue
        emoji, label, score = parsed
        text = f"{title} {path.stem}".lower()
        rel = f"Findings/Cloud/{path.name}"
        link_text = _link_text_for(path)
        for service, kws in service_buckets:
            if any(k in text for k in kws):
                buckets[service].append((score, f"{emoji} {label} {score}/10", rel, link_text))

    written: list[Path] = []
    out_dir = OUTPUT_SUMMARY_DIR / "Cloud"
    out_dir.mkdir(parents=True, exist_ok=True)

    for service, _kws in service_buckets:
        rows = buckets.get(service) or []
        if not rows:
            continue
        rows = sorted(rows, key=lambda x: x[0], reverse=True)
        top_refs = [(r[2], r[3]) for r in rows[:2]]

        actions = []
        if service in {"Key Vault", "Storage Accounts", "Azure SQL", "Storage", "Cloud Storage", "RDS", "Cloud SQL"}:
            actions = [
                f"Restrict network access (private endpoints / firewall) and remove broad exceptions (see [{top_refs[0][1]}]({_rel_link_from_summary(top_refs[0][0])})).",
                f"Enforce least privilege (RBAC) and require strong identity controls (see [{top_refs[-1][1]}]({_rel_link_from_summary(top_refs[-1][0])})).",
                "Enable monitoring/auditing and alert on anomalous access.",
            ]
        elif service in {"Network", "Virtual Machines"}:
            actions = [
                f"Remove broad inbound rules and restrict management access via Bastion/JIT (see [{top_refs[0][1]}]({_rel_link_from_summary(top_refs[0][0])})).",
                "Segment networks and reduce lateral movement paths.",
                "Enforce baselines with policy-as-code and monitor drift.",
            ]
        elif service == "Identity":
            actions = [
                f"Enforce MFA/Conditional Access for privileged roles (see [{top_refs[0][1]}]({_rel_link_from_summary(top_refs[0][0])})).",
                "Use just-in-time elevation (PIM / role-based workflows) and remove standing privilege.",
                "Audit privileged assignments and automate offboarding.",
            ]
        else:
            actions = [
                f"Replace shared/admin credentials with identity-based access (see [{top_refs[0][1]}]({_rel_link_from_summary(top_refs[0][0])})).",
                "Harden deployment paths (CI/CD) and monitor for drift.",
            ]

        findings_lines = []
        for score, overall, rel, link_text in rows:
            link = f"[{link_text}]({_rel_link_from_summary(rel)})"
            m = re.match(r"^(🔴|🟠|🟡|🟢)\s+(Critical|High|Medium|Low)\s+(\d{1,2})/10$", overall)
            if m:
                findings_lines.append(f"- {m.group(1)} **{m.group(2)} {m.group(3)}/10:** {link}")
            else:
                findings_lines.append(f"- {overall}: {link}")

        file_name = service.replace(" ", "_") + ".md"
        out_path = out_dir / file_name
        out_path.write_text(
            "\n".join(
                [
                    f"# 🟣 {service}",
                    "",
                    _summary_mermaid(service).rstrip(),
                    "",
                    "## 🧭 Overview",
                    f"- **Provider:** {provider.title()}",
                    "- **Scope:** Derived from [Cloud findings](../../Findings/Cloud/)",
                    "",
                    "## 🚩 Risk",
                    "Risk is driven by the highest-severity findings for this resource/theme.",
                    "",
                    "## ✅ Actions",
                    *[f"- [ ] {a}" for a in actions],
                    "",
                    "## 📌 Findings",
                    *findings_lines,
                    "",
                ]
            ),
            encoding="utf-8",
        )

        probs = validate_markdown_file(out_path, fix=True)
        errs = [p for p in probs if p.level == "ERROR"]
        if errs:
            raise SystemExit(f"Mermaid validation failed for {out_path}: {errs[0].message}")

        written.append(out_path)

    return written


def run_risk_register() -> Path | None:
    script = ROOT / "Skills" / "risk_register.py"
    if not script.exists():
        return None
    subprocess.run([sys.executable, str(script)], check=True)
    return OUTPUT_SUMMARY_DIR / "Risk Register.xlsx"


def upgrade_existing_draft_findings(out_dir: Path, ts: str) -> int:
    """Bring existing title-only generated findings up to the current template."""

    upgraded = 0
    marker = "draft finding generated from a title-only input"

    for p in sorted(out_dir.glob("*.md")):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lower = text.lower()
        if marker not in lower:
            continue

        # Avoid rewriting files that already look upgraded.
        if (
            "## 🗺️ architecture diagram" in lower
            and "### ⚠️ assumptions" in lower
            and "what’s missing/wrong vs security review" in lower
        ):
            continue

        title = _parse_title(p) or p.stem.replace("_", " ")
        parsed = _parse_overall_score(p)
        score = parsed[2] if parsed else score_for(title)

        write_finding(p, title, score, ts)
        upgraded += 1

    return upgraded


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate draft findings from title-only inputs.")
    parser.add_argument("--provider", required=True, choices=["azure", "aws", "gcp"], help="Cloud provider")
    parser.add_argument(
        "--in-dir",
        required=True,
        help="Input folder (recursively) or a single .txt/.csv list file",
    )
    parser.add_argument("--out-dir", required=True, help="Output folder for generated findings")
    parser.add_argument(
        "--update-knowledge",
        action="store_true",
        help=(
            "Update Knowledge/<Provider>.md and Summary/Cloud/Architecture_<Provider>.md "
            "(and also generate Summary/Cloud per-service summaries + Risk Register)"
        ),
    )
    parser.add_argument(
        "--update-summaries",
        action="store_true",
        help="Generate per-service summaries under Summary/Cloud (implied by --update-knowledge)",
    )
    parser.add_argument(
        "--update-risk-register",
        action="store_true",
        help="Regenerate Summary/Risk Register.xlsx (implied by --update-knowledge)",
    )
    parser.add_argument(
        "--upgrade-existing",
        action="store_true",
        help="Upgrade existing title-only draft findings in --out-dir to the latest template",
    )
    args = parser.parse_args()

    in_dir = (ROOT / args.in_dir).resolve() if not Path(args.in_dir).is_absolute() else Path(args.in_dir)
    out_dir = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)

    if not in_dir.exists():
        raise SystemExit(f"Input path not found: {in_dir}")
    if not (in_dir.is_dir() or in_dir.is_file()):
        raise SystemExit(f"Input path is not a file or folder: {in_dir}")

    ts = now_uk()
    out_dir.mkdir(parents=True, exist_ok=True)

    titles: list[str] = []
    generated = 0
    skipped_dupes = 0

    seen: set[str] = set()
    # Prevent re-creating findings we already generated in the output folder.
    if out_dir.exists():
        for existing in sorted(out_dir.glob("*.md")):
            if existing.is_file():
                try:
                    first = existing.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
                    if first:
                        # First line is typically: "# 🟣 <title>".
                        existing_title = _normalise_title(first[0]).lstrip("🟣 ").strip()
                        seen.add(_dedupe_key(existing_title))
                except OSError:
                    pass

    if args.upgrade_existing:
        upgraded = upgrade_existing_draft_findings(out_dir, ts)
        if upgraded:
            print(f"Upgraded {upgraded} existing draft finding(s) in {out_dir}")

    paths: list[Path]
    if in_dir.is_file():
        paths = [in_dir]
    else:
        paths = sorted(p for p in in_dir.rglob("*") if p.is_file())

    for path in paths:
        if path.name.startswith("."):
            continue
        if path.name in {"README.md", ".gitignore", ".gitkeep"}:
            continue

        extracted = _titles_from_path(path)
        if not extracted:
            continue

        for title in extracted:
            key = _dedupe_key(title)
            if key in seen:
                skipped_dupes += 1
                continue
            seen.add(key)

            titles.append(title)
            score = score_for(title)

            # Preserve old naming for 1-file-per-finding inputs; list files use title-based naming.
            if path.suffix.lower() in {".txt", ".csv"}:
                out_name = titlecase_filename(title)
            else:
                out_name = titlecase_filename(path.stem)

            out_path = _unique_out_path(out_dir, out_name)
            write_finding(out_path, title, score, ts)
            generated += 1

    if args.update_knowledge:
        args.update_summaries = True
        args.update_risk_register = True

    audit_path: Path | None = None

    if args.update_knowledge:
        knowledge_path = ensure_knowledge(args.provider, ts)
        audit_path = update_knowledge_generic(knowledge_path, args.provider, titles, ts)

        arch_path = update_architecture(args.provider, ts)
        if arch_path is not None:
            append_audit_event(
                audit_path,
                [
                    f"- [{ts}] Created/updated architecture diagram.",
                    f"  - Wrote/updated: `{arch_path.relative_to(ROOT)}`.",
                ],
            )
    elif args.update_summaries or args.update_risk_register:
        # Allow summaries/risk register generation without creating/updating Knowledge/.
        audit_path = ensure_audit(args.provider)

    if args.update_summaries:
        summary_paths = update_service_summaries(args.provider, ts)
        if audit_path is not None and summary_paths:
            rels = [f"  - Wrote/updated: `{p.relative_to(ROOT)}`." for p in summary_paths]
            append_audit_event(
                audit_path,
                [f"- [{ts}] Generated per-service cloud summaries.", *rels],
            )

    if args.update_risk_register:
        rr = run_risk_register()
        if audit_path is not None and rr is not None and rr.exists():
            append_audit_event(
                audit_path,
                [
                    f"- [{ts}] Generated the executive risk register.",
                    "  - Ran: `python3 Skills/risk_register.py`.",
                    f"  - Wrote: `{rr.relative_to(ROOT)}`.",
                ],
            )

    msg = f"Generated {generated} finding(s) into {out_dir}"
    if skipped_dupes:
        msg += f" (skipped {skipped_dupes} duplicate title(s))"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
