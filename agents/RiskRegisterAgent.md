# 🟣 Risk Register Agent

## Purpose
Create and maintain the Excel risk register for executive reporting. The register is stored at
`Summary/Risk Register.xlsx` and must stay in sync with all findings in `Findings/`.

## Scope
- Include every finding from `Findings/Cloud/` and `Findings/Code/`.
- Recalculate priorities whenever any finding changes.
- Keep language concise and suitable for Exco and management.

## Output Fields
- **Priority:** Numeric rank (1 is highest priority).
- **Resource Type:** Resource category derived from the finding.
- **Issue:** Short finding title.
- **Risk Score:** Numeric score (1-10).
- **Overall Severity:** Critical, High, Medium, Low.
- **Business Impact:** One-sentence, business-facing impact statement.
- **File Reference:** Path to the finding file.

## Data Sources and Mapping
- **Issue:** Use the finding title from the first heading line (strip the leading emoji and
  `#`).
- **Risk Score:** Parse the numeric value from `- **Overall Score:**` (e.g., `7/10` => 7).
- **Overall Severity:** Parse the severity label from `- **Overall Score:**`.
- **Business Impact:** Use the `## 🛡️ Security Review` summary sentence and rewrite for
  business impact if needed. Keep it concise and do not prefix with “Potential impact”.
- **File Reference:** Use the relative path under `Findings/`.
- **Resource Type:**
  - `AZ-` prefix => Azure
  - `AKS-` prefix => AKS
  - `A0` prefix => Application Code
  - Otherwise => Application

## Prioritisation Rules
- Sort by `Risk Score` descending.
- If scores are equal, order by severity: Critical > High > Medium > Low.
- If still tied, order by Resource Type, then Issue alphabetically.
- Assign `Priority` sequentially starting at 1.

## Workflow
1. Scan `Findings/Cloud/` and `Findings/Code/` for `.md` files.
2. Extract the required fields from each file.
3. Recalculate priority based on current data.
4. Use `Skills/risk_register.py` to generate or update the register.
5. Write the full register to `Summary/Risk Register.xlsx` (overwrite in full).
5. Ensure the sheet has a single header row with filters, bold headers, and frozen first row.

## Quality Checks
- Confirm the register row count equals the total number of findings.
- Spot-check the top 5 priorities align with the highest scores.
- Ensure all file references are valid and point to existing files.

## Notes
- Do not include timestamps in the spreadsheet.
- Keep the business impact concise and free of technical jargon.
- If any finding lacks an `Overall Score`, halt and report the missing file.
