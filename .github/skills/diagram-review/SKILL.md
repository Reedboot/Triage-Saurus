---
name: diagram-review
description: Run Triage-Saurus diagram review (screenshots, threat-model smells, and before/after report).
---

Run the Triage-Saurus diagram review workflow for this repository.

## Prerequisites
Before running, verify:
1. The web UI is reachable: `curl -sf http://127.0.0.1:9000 > /dev/null || echo "Server not running"`
2. Playwright is installed: `python3 -m playwright install chromium`

If the server is not running, instruct the user to start it (e.g. `bash Scripts/start_web.sh`) before proceeding.

## Running the review

Use the orchestrator script with sensible defaults:
```bash
python3 Scripts/Validate/review_generated_diagrams.py --base-url http://127.0.0.1:9000
```

### Key flags
| Flag | Purpose |
|---|---|
| `--repos <name...>` | Limit to specific repo names |
| `--repo-at-a-time` | Strict mode: retry each repo before continuing |
| `--skip-after-pass` | Baseline-only run (no rule-apply + re-scan cycle) |
| `--only-unscanned` | Target repos with no prior scan history |
| `--concurrency <N>` | Parallel scan workers (default: 6) |
| `--scan-complete-timeout-sec <N>` | Per-repo scan timeout in seconds (default: 600) |
| `--opengrep-timeout-sec <N>` | OpenGrep validation timeout (default: 120) |
| `--audit-root <path>` | Override output root (default: `Output/Audit/`) |
| `--headed` | Show browser window (useful for debugging Playwright) |

## Output artifacts
All artifacts are written under:
```
Output/Audit/DiagramReviewSkill_<timestamp>/
  diagram_review_report.md   ← main before/after report
  baseline/                  ← baseline pass summaries + screenshots
  after/                     ← after pass summaries + screenshots (unless --skip-after-pass)
```

## Summarise results
After the run, report:
1. Path to `diagram_review_report.md`
2. Key before/after deltas (orphan issues, hierarchy smells, high-value smells, repos failed)
3. Any OpenGrep detection-rule validation failures (`detection_rule_validation_failed > 0`)
4. Any repos that timed out or failed

## Agent review logic
For the element-level rationale, icon validation, hierarchy expectations, security posture checks,
and investigation checklist, follow the full guidance in `Agents/DiagramReviewSkill.md`.
