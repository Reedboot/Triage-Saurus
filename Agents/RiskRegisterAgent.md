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
- The spreadsheet is **for ExCo + CISO**: board-friendly, minimal columns.
- The generator parses each finding’s `- **Overall Score:** <Severity> <n>/10` line.
- **Priority is global** across Cloud + Code + Repo findings: 1 = most urgent.
  - It is derived deterministically by sorting on **Risk Score (desc)**, then **Severity (desc)**,
    then stable tie-breakers.
- **No AI checks are performed** on the spreadsheet contents; ranking is rules-based.
- **Multi-cloud:** the risk register must correctly classify services across **Azure/AWS/GCP**.
  - If a title clearly names a service (e.g., "Azure Cosmos DB", "AWS S3", "GCP Cloud SQL"), the
    **Resource Type** should reflect that service (not just "Cloud").
  - Keep the classifier mappings (and guardrail warnings) in `Skills/risk_register.py` up to date as
    new services appear.
- The **Business Impact** column is a **single short sentence** for management (e.g.
  "Increased attack surface.", "Data loss or exposure.", "Bypass of authentication.").
  It is derived from the finding title/issue (and optionally `### Summary`) but must not include
  countermeasures or implementation detail.
- The **Issue** column is written in exec-friendly phrasing and should not start with "Risk:".
  - **Repo findings:** titles are often just `Repo <name>`; the risk register should derive the Issue from the repo finding’s `### Summary` so the spreadsheet shows *what’s wrong*, not just the repo name.
- Duplicate Issues are de-duplicated (keeping the highest-scoring entry) to reduce noise.
- Keep `Summary/Risk Register.xlsx` **continuously up to date** with the latest findings
  (regenerate after any finding edit).
