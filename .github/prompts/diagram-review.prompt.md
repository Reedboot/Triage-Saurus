---
description: "Run Triage-Saurus diagram review (screenshots, threat-model smells, and before/after report)."
mode: "agent"
tools: ["codebase", "terminal"]
model: "gpt-5.4-mini"
---

Run the Triage-Saurus diagram review workflow for this repository.

Requirements:
1. Use the existing orchestrator script:
   `python3 Scripts/Validate/review_generated_diagrams.py --base-url http://127.0.0.1:9001`
2. If the user specifies repo filters, include `--repos <repo...>`.
3. If requested, use `--repo-at-a-time` or `--skip-after-pass`.
4. Summarize:
   - output artifact location under `Output/Audit/DiagramReviewSkill_<timestamp>/`
   - key before/after deltas
   - any OpenGrep detection-rule validation failures
