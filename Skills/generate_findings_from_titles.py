#!/usr/bin/env python3
"""Generate draft findings from title-only inputs.

Use this when you have many findings that are only a short title (e.g., from a
scanner export) and you want to quickly create well-formed finding Markdown
files that match the repository templates.

Inputs
- A folder containing files; the generator uses the FIRST LINE of each file as
  the finding title.

Outputs
- Writes finding files to an output folder you specify.
- Optionally updates Knowledge/<Provider>.md and Summary/Cloud/Architecture_<Provider>.md.

Example
  python3 Skills/generate_findings_from_titles.py \
    --provider azure \
    --in-dir "Sample Findings/Cloud" \
    --out-dir "Findings/Cloud" \
    --update-knowledge
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def now_uk() -> str:
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M")


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

- **Description:** {title}
- **Overall Score:** {sev} {score}/10

## 🛡️ Security Review
### Summary
This is a draft finding generated from a title-only input. Validate the affected
resources/scope and confirm whether the exposure is internet-facing and/or impacts
production workloads.

### 🎯 Exploitability
Misconfiguration findings are typically exploitable by either (a) external attackers
when exposure is public, or (b) internal threat actors / compromised identities when
permissions and network paths are overly broad.

### Recommendations
- [ ] {recs[0]} — ⬇️ {score}➡️{reduced_1} (est.)
- [ ] {recs[1]} — ⬇️ {reduced_1}➡️{reduced_2} (est.)

### Considered Countermeasures
- 🔴 Rely on ad-hoc manual configuration — prone to drift and gaps.
- 🟡 Point-in-time remediation only — helps now but without policy, issues often return.
- 🟢 Enforce with policy-as-code + monitoring — reduces recurrence and improves coverage.

### Rationale
The recommendation reduces attack surface and/or blast radius and should align with
provider baseline guidance. Confirm exact control mappings in your environment.

## 🤔 Skeptic
### 🛠️ Dev
- **Score recommendation:** ➡️ Keep (confirm which apps/workloads are impacted).
- **Mitigation note:** Update IaC so the fix persists.

### 🏗️ Platform
- **Score recommendation:** ➡️ Keep (may require policy/networking/SKU changes).
- **Mitigation note:** Roll out guardrails first, then remediate at scale.

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


def ensure_knowledge(provider: str, ts: str) -> Path:
    path = ROOT / "Knowledge" / f"{provider.title()}.md"
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# {provider.title()} Knowledge (Confirmed + Assumptions)

## Confirmed
- [{ts}] Provider selected for triage: {provider.title()}.

## Assumptions
- (none yet)
""",
        encoding="utf-8",
    )
    return path


def update_knowledge_generic(knowledge_path: Path, provider: str, titles: list[str], ts: str) -> None:
    # Only add a minimal note; don't guess specific services cross-provider.
    block = "\n".join([f"- [{ts}] Finding imported (title-only): {t}." for t in titles[:25]])
    note = (
        f"\n- [{ts}] Imported {len(titles)} title-only finding(s) via Skills/generate_findings_from_titles.py."
        + ("\n" + block + (f"\n- [{ts}] (truncated)" if len(titles) > 25 else ""))
    )

    text = knowledge_path.read_text(encoding="utf-8")
    if "Imported" in text and "generate_findings_from_titles.py" in text:
        # avoid repeating on reruns
        return

    # Append to Confirmed section end.
    if "## Confirmed" in text:
        text = text.rstrip() + "\n" + note + "\n"
    else:
        text = text.rstrip() + f"\n\n## Confirmed\n{note}\n"

    knowledge_path.write_text(text, encoding="utf-8")


def update_architecture(provider: str, ts: str) -> None:
    if provider.lower() not in {"azure", "aws", "gcp"}:
        return

    out = ROOT / "Summary" / "Cloud" / f"Architecture_{provider.title()}.md"
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
  Internet[🌐 Internet] --> Cloud[🧩 {provider.title()}]
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate draft findings from title-only inputs.")
    parser.add_argument("--provider", required=True, choices=["azure", "aws", "gcp"], help="Cloud provider")
    parser.add_argument("--in-dir", required=True, help="Input folder containing title files")
    parser.add_argument("--out-dir", required=True, help="Output folder for generated findings")
    parser.add_argument(
        "--update-knowledge",
        action="store_true",
        help="Update Knowledge/<Provider>.md and Summary/Cloud/Architecture_<Provider>.md",
    )
    args = parser.parse_args()

    in_dir = (ROOT / args.in_dir).resolve() if not Path(args.in_dir).is_absolute() else Path(args.in_dir)
    out_dir = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)

    if not in_dir.exists() or not in_dir.is_dir():
        raise SystemExit(f"Input folder not found: {in_dir}")

    ts = now_uk()
    out_dir.mkdir(parents=True, exist_ok=True)

    titles: list[str] = []
    generated = 0

    for path in sorted(in_dir.glob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        title = (first_line[0].strip() if first_line else "").lstrip("# ").strip()
        if not title:
            continue

        titles.append(title)
        score = score_for(title)
        out_name = titlecase_filename(path.stem)
        out_path = out_dir / f"{out_name}.md"
        write_finding(out_path, title, score, ts)
        generated += 1

    if args.update_knowledge:
        knowledge_path = ensure_knowledge(args.provider, ts)
        update_knowledge_generic(knowledge_path, args.provider, titles, ts)
        update_architecture(args.provider, ts)

    print(f"Generated {generated} finding(s) into {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
