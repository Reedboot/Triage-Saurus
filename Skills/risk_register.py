#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import os
from zipfile import ZipFile, ZIP_DEFLATED

# Header fill colour (Excel ARGB): FF1F4E79 (dark blue)

ROOT = Path(__file__).resolve().parents[1]

from output_paths import OUTPUT_FINDINGS_DIR, OUTPUT_SUMMARY_DIR

FINDINGS_DIR = OUTPUT_FINDINGS_DIR
OUTPUT_PATH = OUTPUT_SUMMARY_DIR / "Risk Register.xlsx"

SEVERITY_ORDER = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

ACRONYM_REPLACEMENTS = {
    "RBAC": "access control",
    "API": "interface",
    "AKS": "Kubernetes cluster",
    "NSG": "network firewall",
    "TLS": "encryption",
    "MFA": "multi-factor authentication",
    "SIEM": "security monitoring",
    "Key Vault": "secrets store",
    "Kubernetes": "container platform",
}


@dataclass(frozen=True)
class RiskRow:
    priority: int
    resource_type: str
    issue: str
    risk_score: int
    overall_severity: str
    business_impact: str
    validation_status: str  # "✅ Validated" or "⚠️ Draft - Needs Triage"
    file_reference: str


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def iter_finding_files() -> Iterable[Path]:
    for folder in (FINDINGS_DIR / "Cloud", FINDINGS_DIR / "Code", FINDINGS_DIR / "Repo"):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            yield path


def parse_overall_score(lines: list[str], path: Path) -> tuple[str, int]:
    for line in lines:
        if line.strip().startswith("- **Overall Score:**"):
            match = re.search(r"(Critical|High|Medium|Low)\s+(\d+)/10", line)
            if not match:
                raise ValueError(f"Unable to parse overall score in {path}")
            return match.group(1), int(match.group(2))
    raise ValueError(f"Missing overall score in {path}")


def parse_description(lines: list[str], path: Path) -> str:
    """Extract the Description line which often has more context than title."""
    for line in lines:
        if line.strip().startswith("- **Description:**"):
            return line.replace("- **Description:**", "").strip()
    return ""


def parse_title(lines: list[str], path: Path) -> str:
    if not lines:
        raise ValueError(f"Empty finding: {path}")
    heading = lines[0].lstrip("# ").strip()
    heading = heading.lstrip("🟣 ").strip()
    return heading


def parse_issue(title: str) -> str:
    # Strip leading ID tokens such as AZ-003, Az-003, AKS-01, A01.
    cleaned = re.sub(r"^[A-Za-z]{1,3}-?\\d+\\s+", "", title).strip()

    # Keep the Issue close to the finding title, but drop inline config/value noise.
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()  # remove parenthetical details
    cleaned = re.sub(r"\s*\.\s*and\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned or title


def is_draft_finding(lines: list[str]) -> bool:
    """Check if a finding is a draft (generic boilerplate, not validated)."""
    text = " ".join(lines).lower()
    
    # Check for draft indicators
    draft_indicators = [
        "draft finding generated from a title-only input",
        "this is a draft finding",
        "validate the affected resources/scope",
        "title-only input; needs validation",
    ]
    
    return any(indicator in text for indicator in draft_indicators)


def parse_summary(lines: list[str], path: Path) -> str:
    summary_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+(?:🧾\s+)?Summary\s*$", line.strip()):
            summary_idx = idx + 1
            break
    if summary_idx is None:
        raise ValueError(f"Missing summary section in {path}")

    summary_lines = []
    for line in lines[summary_idx:]:
        if not line.strip():
            if summary_lines:
                break
            continue
        if line.startswith("#"):
            break
        summary_lines.append(line.strip())

    if not summary_lines:
        raise ValueError(f"Summary text missing in {path}")

    summary = " ".join(summary_lines)
    if not summary.endswith("."):
        summary += "."
    return summary


def parse_business_impact(lines: list[str], path: Path) -> str | None:
    """Extract Business Impact section if it exists (typically in repo findings)."""
    impact_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+Business\s+Impact\s*$", line.strip(), re.IGNORECASE):
            impact_idx = idx + 1
            break
    
    if impact_idx is None:
        return None
    
    impact_lines = []
    for line in lines[impact_idx:]:
        if not line.strip():
            if impact_lines:
                break
            continue
        if line.startswith("#"):
            break
        impact_lines.append(line.strip())
    
    if not impact_lines:
        return None
    
    impact = " ".join(impact_lines)
    
    # Extract first sentence if it's long
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', impact) if s.strip()]
    if sentences:
        return sentences[0]
    
    return impact if len(impact) < 200 else impact[:197] + "..."


def parse_risk_assessment(lines: list[str], path: Path) -> str | None:
    """Extract Risk Assessment section if it exists (typically in repo findings)."""
    risk_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+Risk\s+Assessment\s*$", line.strip(), re.IGNORECASE):
            risk_idx = idx + 1
            break
    
    if risk_idx is None:
        return None
    
    risk_lines = []
    for line in lines[risk_idx:]:
        if not line.strip():
            if risk_lines:
                break
            continue
        if line.startswith("#"):
            break
        risk_lines.append(line.strip())
    
    if not risk_lines:
        return None
    
    risk = " ".join(risk_lines)
    
    # Extract the key issue after the score
    # Format: "**Medium (6/10)** — <actual issue>. Primary risk is..."
    # We want the full statement before "Primary risk" or first period
    match = re.search(r'—\s*(.+?)(?:\.\s+Primary risk|\.\s+Mitigating)', risk)
    if match:
        issue = match.group(1).strip()
        return issue
    
    # Fallback: extract text after score and before first period
    match2 = re.search(r'—\s*(.+?)\.', risk)
    if match2:
        return match2.group(1).strip()
    
    return None


def parse_key_evidence(lines: list[str], path: Path) -> str:
    """Extract Key Evidence section for additional resource type context."""
    evidence_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+(?:🔎\s+)?Key\s+Evidence\s*$", line.strip(), re.IGNORECASE):
            evidence_idx = idx + 1
            break
    
    if evidence_idx is None:
        return ""
    
    evidence_lines = []
    for line in lines[evidence_idx:]:
        if line.startswith("#"):
            break
        if line.strip() and not line.strip().startswith("-"):
            evidence_lines.append(line.strip())
        if len(evidence_lines) >= 5:  # Limit to first few lines
            break
    
    return " ".join(evidence_lines)


def parse_applicability(lines: list[str], path: Path) -> str:
    """Extract Applicability section for additional resource type context."""
    app_idx = None
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+(?:✅\s+)?Applicability\s*$", line.strip(), re.IGNORECASE):
            app_idx = idx + 1
            break
    
    if app_idx is None:
        return ""
    
    app_lines = []
    for line in lines[app_idx:]:
        if line.startswith("#"):
            break
        if line.strip():
            app_lines.append(line.strip())
        if len(app_lines) >= 5:  # Limit to first few lines
            break
    
    return " ".join(app_lines)


def _classify_business_impact(text: str) -> str:
    t = text.lower()

    # Ordered from most-specific to most-general.
    if any(k in t for k in ["ddos", "denial of service", "dos"]):
        return "Denial of service."

    if any(k in t for k in ["bypass", "authentication", "auth", "mfa", "entra authentication only", "active directory only"]):
        return "Bypass of authentication."

    if any(k in t for k in ["owner", "rbac", "privilege", "privileged", "admin", "permissions", "role"]):
        return "Unauthorised access to critical systems."

    if any(k in t for k in ["audit", "logging", "logs", "activity log", "alert", "retention", "monitor", "defender", "edr"]):
        return "Difficulty tracing actions."

    if any(k in t for k in [
        "public network access",
        "public access",
        "anonymous",
        "storage",
        "blob",
        "bucket",
        "key vault",
        "secret",
        "keys",
        "certificate",
        "encryption",
        "tls",
        "tde",
        "secure transfer",
        "backup",
        "recovery",
    ]):
        return "Data loss or exposure."

    if any(k in t for k in ["management ports", "ssh", "rdp", "internet facing", "public ip", "open ports", "nsg", "firewall rule"]):
        return "Increased attack surface."

    return "Increased security risk."


def to_business_impact(summary: str, issue: str) -> str:
    # Business Impact is a single short, management-friendly sentence.
    base = "" if "draft finding generated from a title-only input" in summary.lower() else summary.strip()

    for term, replacement in ACRONYM_REPLACEMENTS.items():
        base = base.replace(term, replacement)

    base = re.sub(r"\*\*", "", base)
    base = base.replace("`", "")
    base = re.sub(r"^if not addressed\s*[,:\-]?\s*", "", base, flags=re.IGNORECASE)

    # Classify using both issue + optional summary context, but output only the label.
    return _classify_business_impact(f"{issue}\n{base}")


def to_exec_risk_issue(issue: str, impact_label: str) -> str:
    # Exec-friendly issue phrasing + a very brief "why".
    s = issue.strip().rstrip(".")

    # Heuristic rewrites from compliance wording ("should …") into a risk statement.
    rules: list[tuple[str, str]] = [
        # Special-case JIT wording: convert "protected with JIT" to a "missing JIT" statement.
        (r"\bprotected with just[- ]in[- ]time network access control\b", "missing just-in-time access controls (no JIT)"),
        (r"\bprotected with just[- ]in[- ]time\b", "missing just-in-time access controls (no JIT)"),
        (r"\bjust[- ]in[- ]time network access control\b", "just-in-time access controls"),
        (r"\bshould be enabled\b", "is not enabled"),
        (r"\bshould be disabled\b", "is enabled"),
        (r"\bshould be disallowed\b", "is allowed"),
        (r"\bshould disable\b", "does not disable"),
        (r"\bshould not allow\b", "allows"),
        (r"\bshould not be\b", "is"),
        (r"\bshould have\b", "does not have"),
        (r"\bshould use only\b", "does not enforce"),
        (r"\bshould use\b", "does not use"),
        (r"\bshould prevent\b", "does not prevent"),
        (r"\bshould be restricted\b", "is not restricted"),
        (r"\bshould be stored\b", "is not stored"),
        (r"\bshould be retained\b", "is not retained"),
        (r"\bshould exist\b", "is missing"),
        (r"\bshould be\b", "is not"),
    ]
    for pat, repl in rules:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)

    if re.search(r"\bshould\b", s, flags=re.IGNORECASE):
        s = re.sub(r"\bshould\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()

    # Slightly tailor "why" for common patterns without turning it into a remediation instruction.
    if re.search(r"\bno jit\b|\bjust[- ]in[- ]time\b", issue, flags=re.IGNORECASE):
        why = "misses an opportunity to reduce exposure of management access"
    else:
        why = {
            "Increased attack surface.": "creates an internet-reachable entry point",
            "Data loss or exposure.": "raises likelihood of data exposure",
            "Bypass of authentication.": "weakens access controls",
            "Unauthorised access to critical systems.": "enables privilege misuse",
            "Difficulty tracing actions.": "reduces auditability",
            "Denial of service.": "can disrupt service availability",
        }.get(impact_label, "indicates a control gap")

    s = s[:1].upper() + s[1:] if s else s

    out = f"{s} — {why}."
    out = out.replace(" are not missing ", " are missing ")
    out = out.replace(" is not missing ", " is missing ")
    out = out.replace(" are not not ", " are not ")
    out = out.replace(" is not not ", " is not ")
    out = out.replace(" ports is ", " ports are ")
    out = out.replace(" virtual machines is ", " virtual machines are ")
    out = out.replace(" registries allows ", " registries allow ")
    out = re.sub(r"\s+", " ", out)
    return out


def resource_type_from_path(path: Path, title: str, issue: str = "", evidence: str = "", applicability: str = "", description: str = "") -> str:
    parts = {p.lower() for p in path.parts}

    if "code" in parts:
        return "Application Code"
    if "repo" in parts:
        return "Repository"
    if "cloud" not in parts:
        return "Application"

    # Check title, filename, description, issue text, evidence, and applicability for resource type keywords
    t = f"{title} {path.stem} {description} {issue} {evidence} {applicability}".lower()

    # Check for specific Azure/cloud services first (with provider prefixes)
    # Azure subscription/tenant-level controls
    if any(k in t for k in ["azure subscription", "subscriptions should", "activity log", "log profile"]):
        return "Subscription"
    if any(k in t for k in ["flow log", "network watcher"]):
        return "Network Watcher"
    
    # Azure compute and services
    if any(k in t for k in ["virtual machine", "virtual machines", "vm", "management ports", "rdp", "ssh", "windows machines", "linux machines", "bootloader"]):
        return "Virtual Machine"
    if any(k in t for k in ["event hub", "eventhub", "azure event hub"]):
        return "Event Hub"
    if any(k in t for k in ["logic app", "logic apps", "azure logic app"]):
        return "Logic App"
    if any(k in t for k in ["key vault", "keyvault", "azure key vault", "secret", "secrets", "keys"]):
        return "Key Vault"
    if any(k in t for k in ["cosmos db", "cosmosdb", "azure cosmos db", "azure cosmos database"]):
        return "Cosmos DB"
    if any(k in t for k in ["azure data explorer", "kusto"]):
        return "Azure Data Explorer"
    if any(k in t for k in ["microsoft foundry", "foundry"]):
        return "Microsoft Foundry"
    if any(k in t for k in ["service fabric", "azure service fabric"]):
        return "Service Fabric"

    # AWS/GCP common services
    if any(k in t for k in ["s3", "amazon s3"]):
        return "S3"
    if any(k in t for k in ["rds", "aurora"]):
        return "RDS"
    if any(k in t for k in ["ec2", "elastic compute cloud"]):
        return "EC2"
    if any(k in t for k in ["iam", "identity and access management"]):
        return "IAM"
    if any(k in t for k in ["kms", "key management service"]):
        return "KMS"
    if any(k in t for k in ["eks"]):
        return "EKS"

    if any(k in t for k in ["cloud sql"]):
        return "Cloud SQL"
    if any(k in t for k in ["gke"]):
        return "GKE"
    if any(k in t for k in ["cloud run"]):
        return "Cloud Run"
    if any(k in t for k in ["artifact registry"]):
        return "Artifact Registry"
    if any(k in t for k in ["storage account", "storage accounts", "azure storage account", "blob", "shared key", "secure transfer"]):
        return "Storage Account"
    # Check SQL Server before generic Database (more specific)
    if any(k in t for k in ["sql server", "sql servers"]):
        return "SQL Server"
    if any(k in t for k in ["sql database", "azure sql", "tde", "postgresql", "mysql", "mariadb"]):
        return "Database"
    if any(k in t for k in ["kubernetes", "aks", "azure kubernetes"]):
        return "Kubernetes"
    if any(k in t for k in ["container registry", "container registries", "acr", "azure container registry", "image registry"]):
        return "Container Registry"
    # Check Microsoft Defender before App Service (it's a security service, not an app)
    if any(k in t for k in ["microsoft defender", "defender for"]):
        return "Microsoft Defender"
    # Check Function App and Web App before App Service (more specific)
    if any(k in t for k in ["function app", "function apps"]):
        return "Function App"
    if any(k in t for k in ["web app", "web apps"]):
        return "Web App"
    if any(k in t for k in ["app service"]):
        return "App Service"
    if any(k in t for k in ["api management"]):
        return "API Management"
    if any(k in t for k in ["application gateway", "waf"]):
        return "Application Gateway"
    if any(k in t for k in ["nsg", "network security group", "vnet", "virtual network", "ddos", "flow logs", "network watcher", "azure firewall"]):
        return "Networking"
    if any(k in t for k in ["entra", "mfa", "owner", "rbac", "privilege", "permissions", "role"]):
        return "Identity & Access"

    # Fallback: try to extract a service-like phrase from common cloud wording.
    # Example: "Azure Cosmos DB should ..." -> "Cosmos DB".
    m = re.search(r"\b(?:azure|aws|gcp)\s+(.+?)\s+(should|must)\b", title, flags=re.IGNORECASE)
    if m:
        svc = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        if svc:
            return svc

    return "Cloud"


def _normalise_issue_key(issue: str) -> str:
    s = issue.strip().lower()

    # Treat common Linux "dash" backup files as the same underlying issue (e.g. /etc/shadow-).
    s = re.sub(r"(/etc/(?:shadow|gshadow|passwd|group))\-(?=\s)", r"\1", s)

    s = re.sub(r"\s+", " ", s)
    s = s.rstrip(".")
    return s


def _warn_on_missed_service_classification(title: str, resource_type: str) -> None:
    # Non-fatal guardrail: if a finding title clearly names a service but we fell back to "Cloud",
    # emit a warning so the mapping can be improved.
    t = title.lower()
    candidates = [
        ("cosmos db", "Cosmos DB"),
        ("cosmosdb", "Cosmos DB"),
        ("azure data explorer", "Azure Data Explorer"),
        ("microsoft foundry", "Microsoft Foundry"),
        ("service fabric", "Service Fabric"),
        ("aws ", "AWS service"),
        ("gcp ", "GCP service"),
    ]
    for needle, expected in candidates:
        if needle in t and resource_type == "Cloud":
            print(
                f"WARN: Risk register Resource Type fell back to 'Cloud' but title contains '{expected}': {title}",
                file=sys.stderr,
            )


def _is_repo_finding(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return "findings" in parts and "repo" in parts


def _repo_issue_from_summary(title: str, summary: str) -> str:
    # Repo finding titles are often just the repo name, so use the summary to build an exec-facing issue.
    # Title format is typically "Repo: terraform-xyz" or just "terraform-xyz"
    repo = re.sub(r"^repo\s*:\s*", "", title.strip(), flags=re.IGNORECASE).strip() or title.strip()

    # Remove generic boilerplate if present
    summary = re.sub(r"^This repository manages \S+ infrastructure using Terraform\.?\s*", "", summary, flags=re.IGNORECASE).strip()
    
    # Split into sentences; prefer one that signals risk (risk/secret/exposure/supply-chain).
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()]
    pick = sentences[0] if sentences else summary.strip()
    for s in sentences[:3]:
        if re.search(r"\brisk\b|secret|expos|supply[- ]chain|pipeline|state|control|critical|misconfig", s, flags=re.IGNORECASE):
            pick = s
            break

    pick = re.sub(r"^this repo\b\s*", "", pick, flags=re.IGNORECASE).strip()
    pick = pick.rstrip(".")
    
    # If still too generic or empty, use the repo name only
    if not pick or len(pick) < 20 or pick.lower().startswith("this"):
        return repo
    
    # No "Repo" prefix needed - Resource Type column already shows "Repository"
    return f"{repo}: {pick}".strip()


def _strip_markdown_formatting(text: str) -> str:
    """Remove markdown formatting from text for Excel display."""
    # Remove bold (**text**)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Remove italic (*text* or _text_)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    # Remove inline code (`text`)
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text


def _strip_resource_type_prefix(issue: str, resource_type: str) -> str:
    """Remove redundant resource type mention from start of issue text."""
    if not resource_type or resource_type in ["Cloud", "Application", "Application Code"]:
        return issue
    
    # Build patterns to match (singular and plural forms)
    patterns = [
        resource_type.lower(),  # exact match
        resource_type.lower().rstrip('s') + 's',  # plural
        resource_type.lower() + 's',  # plural
    ]
    
    # Special cases for compound names
    if resource_type == "Container Registry":
        patterns.extend(["container registries", "container registry"])
    elif resource_type == "Function App":
        patterns.extend(["function apps", "function app"])
    elif resource_type == "Storage Account":
        patterns.extend(["storage accounts", "storage account"])
    elif resource_type == "Key Vault":
        patterns.extend(["key vaults", "key vault"])
    elif resource_type == "Virtual Machine":
        patterns.extend(["virtual machines", "virtual machine", "vms", "vm"])
    
    issue_lower = issue.lower()
    for pattern in patterns:
        # Match at start of string, followed by space or punctuation
        if issue_lower.startswith(pattern + " ") or issue_lower.startswith(pattern + "'"):
            # Remove the prefix and capitalize the first letter
            remaining = issue[len(pattern):].strip()
            if remaining:
                return remaining[0].upper() + remaining[1:]
    
    return issue


def build_rows() -> list[RiskRow]:
    rows: list[RiskRow] = []
    for path in iter_finding_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        title = parse_title(lines, path)
        severity, score = parse_overall_score(lines, path)
        summary = parse_summary(lines, path)
        
        # Extract additional context for better resource type classification
        description = parse_description(lines, path)
        evidence = parse_key_evidence(lines, path)
        applicability = parse_applicability(lines, path)

        if _is_repo_finding(path):
            # For repo findings, prefer Risk Assessment > Business Impact > Summary
            risk_assessment_text = parse_risk_assessment(lines, path)
            business_impact_text = parse_business_impact(lines, path)
            
            if risk_assessment_text:
                issue_text = risk_assessment_text
            elif business_impact_text:
                issue_text = business_impact_text
            else:
                issue_text = summary
            
            issue = _repo_issue_from_summary(title, issue_text)
        else:
            issue = parse_issue(title)

        impact = to_business_impact(summary, issue)
        exec_issue = to_exec_risk_issue(issue, impact)
        # Use full context from finding file for better resource type classification
        resource_type = resource_type_from_path(path, title, issue, evidence, applicability, description)
        _warn_on_missed_service_classification(title, resource_type)
        
        # Strip redundant resource type prefix from issue text
        exec_issue = _strip_resource_type_prefix(exec_issue, resource_type)
        
        # Strip markdown formatting for Excel display
        exec_issue = _strip_markdown_formatting(exec_issue)
        
        # Check if this is a draft finding
        validation_status = "⚠️ Draft - Needs Triage" if is_draft_finding(lines) else "✅ Validated"

        if _is_repo_finding(path) and re.fullmatch(r"Repo\s+[^:]+", issue, flags=re.IGNORECASE):
            print(
                f"WARN: Repo finding Issue is too generic (check ### Summary): {path.relative_to(ROOT)}",
                file=sys.stderr,
            )

        rows.append(
            RiskRow(
                priority=0,
                resource_type=resource_type,
                issue=exec_issue,
                risk_score=score,
                overall_severity=severity,
                business_impact=impact,
                validation_status=validation_status,
                file_reference=str(path.relative_to(ROOT)),
            )
        )

    rows.sort(
        key=lambda r: (
            -r.risk_score,
            -SEVERITY_ORDER.get(r.overall_severity, 0),
            r.resource_type,
            r.issue,
        )
    )

    # Remove duplicate Issues (keep the highest-scoring row due to sort order).
    deduped: list[RiskRow] = []
    seen: set[str] = set()
    for row in rows:
        key = _normalise_issue_key(row.issue)
        if key in seen:
            continue
        deduped.append(row)
        seen.add(key)

    prioritised: list[RiskRow] = []
    for idx, row in enumerate(deduped, start=1):
        prioritised.append(
            row.__class__(
                priority=idx,
                resource_type=row.resource_type,
                issue=row.issue,
                risk_score=row.risk_score,
                overall_severity=row.overall_severity,
                business_impact=row.business_impact,
                validation_status=row.validation_status,
                file_reference=row.file_reference,
            )
        )
    return prioritised


def build_shared_strings(rows: list[RiskRow], headers: list[str]) -> tuple[list[str], dict[str, int]]:
    values: list[str] = []
    index: dict[str, int] = {}

    def add(value: str) -> int:
        if value not in index:
            index[value] = len(values)
            values.append(value)
        return index[value]

    for header in headers:
        add(header)

    for row in rows:
        add(row.resource_type)
        add(row.issue)
        add(row.overall_severity)
        add(row.business_impact)
        add(row.validation_status)
        add(row.file_reference)
    return values, index


def cell(shared: dict[str, int], value: str, style: int | None = None) -> str:
    s_attr = f" s=\"{style}\"" if style is not None else ""
    return f"<c t=\"s\"{s_attr}><v>{shared[value]}</v></c>"


def number_cell(value: int, style: int | None = None) -> str:
    s_attr = f" s=\"{style}\"" if style is not None else ""
    return f"<c t=\"n\"{s_attr}><v>{value}</v></c>"


def build_sheet_xml(rows: list[RiskRow], shared_index: dict[str, int]) -> str:
    headers = [
        "Priority",
        "Resource Type",
        "Issue",
        "Risk Score",
        "Overall Severity",
        "Business Impact",
        "Status",
        "File Reference",
    ]

    header_cells = "".join(cell(shared_index, h, style=2) for h in headers)
    header_row = f"<row r=\"1\">{header_cells}</row>"

    data_rows = []
    for idx, row in enumerate(rows, start=2):
        cells = [
            number_cell(row.priority),
            cell(shared_index, row.resource_type),
            cell(shared_index, row.issue),
            number_cell(row.risk_score),
            cell(shared_index, row.overall_severity),
            cell(shared_index, row.business_impact),
            cell(shared_index, row.validation_status),
            cell(shared_index, row.file_reference),
        ]
        data_rows.append(f"<row r=\"{idx}\">{''.join(cells)}</row>")

    rows_xml = header_row + "".join(data_rows)

    sheet_xml = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">
  <sheetViews>
    <sheetView tabSelected=\"1\" workbookViewId=\"0\">
      <pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight=\"15\"/>
  <cols>
    <col min=\"1\" max=\"1\" width=\"10\" customWidth=\"1\"/>
    <col min=\"2\" max=\"2\" width=\"16\" customWidth=\"1\"/>
    <col min=\"3\" max=\"3\" width=\"40\" customWidth=\"1\"/>
    <col min=\"4\" max=\"4\" width=\"12\" customWidth=\"1\"/>
    <col min=\"5\" max=\"5\" width=\"16\" customWidth=\"1\"/>
    <col min=\"6\" max=\"6\" width=\"60\" customWidth=\"1\"/>
    <col min=\"7\" max=\"7\" width=\"45\" customWidth=\"1\"/>
  </cols>
  <sheetData>
    {rows_xml}
  </sheetData>
  <autoFilter ref=\"A1:G1\"/>
</worksheet>
"""
    return sheet_xml


def build_styles_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">
  <fonts count=\"3\">
    <font>
      <sz val=\"11\"/>
      <color theme=\"1\"/>
      <name val=\"Calibri\"/>
      <family val=\"2\"/>
    </font>
    <font>
      <b/>
      <sz val=\"11\"/>
      <color theme=\"1\"/>
      <name val=\"Calibri\"/>
      <family val=\"2\"/>
    </font>
    <font>
      <b/>
      <sz val=\"11\"/>
      <color rgb=\"FFFFFFFF\"/>
      <name val=\"Calibri\"/>
      <family val=\"2\"/>
    </font>
  </fonts>
  <fills count=\"3\">
    <fill>
      <patternFill patternType=\"none\"/>
    </fill>
    <fill>
      <patternFill patternType=\"gray125\"/>
    </fill>
    <fill>
      <patternFill patternType=\"solid\">
        <fgColor rgb=\"FF1F4E79\"/>
        <bgColor rgb=\"FF1F4E79\"/>
      </patternFill>
    </fill>
  </fills>
  <borders count=\"1\">
    <border>
      <left/><right/><top/><bottom/><diagonal/>
    </border>
  </borders>
  <cellStyleXfs count=\"1\">
    <xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/>
  </cellStyleXfs>
  <cellXfs count=\"3\">
    <xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/>
    <xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/>
    <xf numFmtId=\"0\" fontId=\"2\" fillId=\"2\" borderId=\"0\" xfId=\"0\" applyFont=\"1\" applyFill=\"1\" applyAlignment=\"1\">
      <alignment horizontal=\"center\" vertical=\"center\"/>
    </xf>
  </cellXfs>
</styleSheet>
"""


def build_shared_strings_xml(strings: list[str]) -> str:
    items = "".join(f"<si><t>{xml_escape(s)}</t></si>" for s in strings)
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"{len(strings)}\" uniqueCount=\"{len(strings)}\">
  {items}
</sst>
"""


def build_workbook_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">
  <sheets>
    <sheet name=\"Risk Register\" sheetId=\"1\" r:id=\"rId1\"/>
  </sheets>
</workbook>
"""


def build_content_types_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
  <Default Extension=\"xml\" ContentType=\"application/xml\"/>
  <Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>
  <Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>
  <Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>
  <Override PartName=\"/xl/sharedStrings.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml\"/>
</Types>
"""


def build_root_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>
</Relationships>
"""


def build_workbook_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>
  <Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>
  <Relationship Id=\"rId3\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings\" Target=\"sharedStrings.xml\"/>
</Relationships>
"""


def write_xlsx(rows: list[RiskRow], output_path: Path) -> None:
    headers = [
        "Priority",
        "Resource Type",
        "Issue",
        "Risk Score",
        "Overall Severity",
        "Business Impact",
        "Status",
        "File Reference",
    ]

    strings, index = build_shared_strings(rows, headers)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with ZipFile(tmp_path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", build_content_types_xml())
        zf.writestr("_rels/.rels", build_root_rels_xml())
        zf.writestr("xl/workbook.xml", build_workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", build_workbook_rels_xml())
        zf.writestr("xl/styles.xml", build_styles_xml())
        zf.writestr("xl/sharedStrings.xml", build_shared_strings_xml(strings))
        zf.writestr("xl/worksheets/sheet1.xml", build_sheet_xml(rows, index))

    try:
        os.replace(tmp_path, output_path)
    except PermissionError:
        # Common when the spreadsheet is open/locked by another process.
        alt = output_path.with_name(output_path.stem + " (new)" + output_path.suffix)
        os.replace(tmp_path, alt)
        print(f"WARNING: {output_path} is locked; wrote updated file to {alt}", file=sys.stderr)


def main() -> int:
    rows = build_rows()
    if not rows:
        print("No findings found.")
        return 1
    write_xlsx(rows, OUTPUT_PATH)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
