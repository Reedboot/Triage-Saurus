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


def resource_type_from_path(path: Path, title: str) -> str:
    parts = {p.lower() for p in path.parts}

    if "code" in parts:
        return "Application Code"
    if "repo" in parts:
        return "Repository"
    if "cloud" not in parts:
        return "Application"

    t = f"{title} {path.stem}".lower()

    if any(k in t for k in ["virtual machine", "virtual machines", "vm", "management ports", "rdp", "ssh"]):
        return "Virtual Machine"
    if any(k in t for k in ["key vault", "keyvault", "secret", "secrets", "keys"]):
        return "Key Vault"
    if any(k in t for k in ["cosmos db", "cosmosdb"]):
        return "Cosmos DB"
    if any(k in t for k in ["azure data explorer", "kusto"]):
        return "Azure Data Explorer"
    if any(k in t for k in ["microsoft foundry", "foundry"]):
        return "Microsoft Foundry"
    if any(k in t for k in ["service fabric"]):
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
    if any(k in t for k in ["storage account", "storage accounts", "blob", "shared key", "secure transfer"]):
        return "Storage Account"
    if any(k in t for k in ["sql", "tde", "postgresql", "mysql", "mariadb"]):
        return "Database"
    if any(k in t for k in ["kubernetes", "aks"]):
        return "Kubernetes"
    if any(k in t for k in ["container registry", "acr", "image registry"]):
        return "Container Registry"
    if any(k in t for k in ["app service", "web app", "function app"]):
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
    repo = re.sub(r"^repo\s+", "", title.strip(), flags=re.IGNORECASE).strip() or title.strip()

    # Split into sentences; prefer one that signals risk (risk/secret/exposure/supply-chain).
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s.strip()]
    pick = sentences[0] if sentences else summary.strip()
    for s in sentences[:3]:
        if re.search(r"\brisk\b|secret|expos|supply[- ]chain|pipeline|state", s, flags=re.IGNORECASE):
            pick = s
            break

    pick = re.sub(r"^this repo\b\s*", "", pick, flags=re.IGNORECASE).strip()
    pick = pick.rstrip(".")
    return f"Repo {repo}: {pick}".strip()


def build_rows() -> list[RiskRow]:
    rows: list[RiskRow] = []
    for path in iter_finding_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        title = parse_title(lines, path)
        severity, score = parse_overall_score(lines, path)
        summary = parse_summary(lines, path)

        if _is_repo_finding(path):
            issue = _repo_issue_from_summary(title, summary)
        else:
            issue = parse_issue(title)

        impact = to_business_impact(summary, issue)
        exec_issue = to_exec_risk_issue(issue, impact)
        resource_type = resource_type_from_path(path, title)
        _warn_on_missed_service_classification(title, resource_type)

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
