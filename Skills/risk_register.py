#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FINDINGS_DIR = ROOT / "Findings"
OUTPUT_PATH = ROOT / "Summary" / "Risk Register.xlsx"

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
    for folder in (FINDINGS_DIR / "Cloud", FINDINGS_DIR / "Code"):
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


def parse_summary(lines: list[str], path: Path) -> str:
    summary_idx = None
    for idx, line in enumerate(lines):
        if line.strip() == "### Summary":
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


def to_business_impact(summary: str) -> str:
    impact = summary
    for term, replacement in ACRONYM_REPLACEMENTS.items():
        impact = impact.replace(term, replacement)
    # Keep impact short and board-friendly.
    impact = impact.split(".")[0].strip()
    if "public network access" in impact.lower():
        impact = "Public access raises the risk of unauthorised secrets access"
    return impact


def resource_type_from_name(name: str) -> str:
    upper = name.upper()
    if upper.startswith("AZ-"):
        return "Azure"
    if upper.startswith("AKS-"):
        return "AKS"
    if upper.startswith("A0"):
        return "Application Code"
    return "Application"


def build_rows() -> list[RiskRow]:
    rows: list[RiskRow] = []
    for path in iter_finding_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        title = parse_title(lines, path)
        severity, score = parse_overall_score(lines, path)
        summary = parse_summary(lines, path)
        impact = to_business_impact(summary)
        resource_type = resource_type_from_name(path.stem)
        rows.append(
            RiskRow(
                priority=0,
                resource_type=resource_type,
                issue=title,
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

    prioritised = []
    for idx, row in enumerate(rows, start=1):
        prioritised.append(row.__class__(
            priority=idx,
            resource_type=row.resource_type,
            issue=row.issue,
            risk_score=row.risk_score,
            overall_severity=row.overall_severity,
            business_impact=row.business_impact,
            file_reference=row.file_reference,
        ))
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

    header_cells = "".join(cell(shared_index, h, style=1) for h in headers)
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
  <fonts count=\"2\">
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
  </fonts>
  <fills count=\"1\">
    <fill>
      <patternFill patternType=\"none\"/>
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
  <cellXfs count=\"2\">
    <xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/>
    <xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/>
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
    with ZipFile(output_path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", build_content_types_xml())
        zf.writestr("_rels/.rels", build_root_rels_xml())
        zf.writestr("xl/workbook.xml", build_workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", build_workbook_rels_xml())
        zf.writestr("xl/styles.xml", build_styles_xml())
        zf.writestr("xl/sharedStrings.xml", build_shared_strings_xml(strings))
        zf.writestr("xl/worksheets/sheet1.xml", build_sheet_xml(rows, index))


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
