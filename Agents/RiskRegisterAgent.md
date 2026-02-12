# 🟣 Risk Register Agent

## Role
Generate the executive risk register spreadsheet from findings.

## Input
- Findings under:
  - `Findings/Cloud/*.md`
  - `Findings/Code/*.md`

## Output
- `Summary/Risk Register.xlsx`

## How to run
From repo root:
```bash
python3 Skills/risk_register.py
```

## Notes
- The generator parses each finding’s `- **Overall Score:** <Severity> <n>/10` line.
- The **Business Impact** column is a **single short sentence** for management (e.g.
  "Increased attack surface.", "Data loss or exposure.", "Bypass of authentication.").
  It is derived from the finding title/issue (and optionally `### Summary`) but should not
  include countermeasures or implementation detail.
- The spreadsheet header row is styled (blue) for readability; keep the content concise and
  board-friendly.
- Ensure findings are well-formed before generating.
- Keep `Summary/Risk Register.xlsx` **continuously up to date** with the latest findings
  (regenerate after any finding edit).
