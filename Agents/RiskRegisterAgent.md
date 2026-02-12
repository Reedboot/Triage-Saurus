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
- **Priority is global** across Cloud + Code + Repo findings: 1 = most urgent.
- The **Business Impact** column is a **single short sentence** for management (e.g.
  "Increased attack surface.", "Data loss or exposure.", "Bypass of authentication.").
  It is derived from the finding title/issue (and optionally `### Summary`) but should not
  include countermeasures or implementation detail.
- The **Issue** column is written in exec-friendly phrasing and should not start with "Risk:".
- Duplicate Issues are de-duplicated (keeping the highest-scoring entry) to avoid noisy repeats.
  - Note: this is a *presentation* de-dupe; bulk finding generation should also avoid creating duplicate findings.
- The spreadsheet header row is styled (blue) for readability; keep the content concise and
  board-friendly.
- Ensure findings are well-formed before generating.
- Keep `Summary/Risk Register.xlsx` **continuously up to date** with the latest findings
  (regenerate after any finding edit).
