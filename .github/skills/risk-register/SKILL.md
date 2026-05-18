---
name: risk-register
description: Generate or refresh the executive risk register spreadsheet aggregating all findings with severity scores, owners, and remediation priorities.
---

Generate or refresh the risk register after triage (or score changes) using findings from the DB.
Full agent guidance is in `Agents/RiskRegisterAgent.md`.

## Prerequisites
At least Phase 4 (enrichment) must be complete for the findings to have scores. Phase 5 (skeptic reviews) should ideally be complete so final scores are used.

Verify experiment state with:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```

## Generating the risk register

```bash
python3 Scripts/Utils/risk_register.py
```

The script uses hardcoded output paths from `Scripts/Utils/output_paths.py` — no flags required.

### Auto-watch mode (regenerate on score changes)
```bash
python3 Scripts/Utils/watch_risk_register.py
```
Watches `Output/Findings` for score changes and regenerates the register automatically — useful while skeptic reviews are running.

### Watch mode flags
| Flag | Purpose |
|---|---|
| `--findings-dir <path>` | Directory to watch (default: `Output/Findings`) |
| `--interval <sec>` | Poll interval in seconds |
| `--debounce <sec>` | Debounce delay before regenerating |
| `--once` | Run once and exit (no watch loop) |

## Output artifacts
```
Output/Summary/
  Risk Register.xlsx    ← executive spreadsheet (sortable by score, owner, resource)
```

## When to re-run
Re-run the risk register after any of:
- Skeptic review completes (scores change)
- Human feedback adjusts a score (`Score change:` note added to finding)
- New findings are added from a re-scan
- A finding is dismissed (score drops to 0 or finding is removed)

## Score change audit trail
When a score changes, the reason must be documented in the finding file:
```
Score change: 5/10 ➜ 7/10 — confirmed internet-facing prod exposure
```
The risk register will reflect the latest score from the DB; the audit trail is in the finding MD.

## Agent review logic
`Agents/RiskRegisterAgent.md`
