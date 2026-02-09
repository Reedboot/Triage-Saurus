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
- Ensure findings are well-formed before generating.
