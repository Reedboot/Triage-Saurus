# Missing Scripts Backlog

These scripts are referenced in agent instruction files or documentation but do not
exist in the repository. They represent either scripts that were deleted without
updating agent instructions, or planned scripts that were never implemented.

**Do not remove references to these scripts from agent files without implementing
them first** — the references describe intended agent behaviour.

---

## Scripts to Implement

| Script | Referenced In | Purpose |
|--------|--------------|---------|
| `Scripts/clear_session.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `README.md` | Delete per-session artifacts under `Output/Findings/`, `Output/Knowledge/`, `Output/Summary/` |
| `Scripts/extract_finding_scores.py` | `Agents/Instructions.md`, `Agents/CodeSummaryAgent.md`, `Agents/RiskRegisterAgent.md`, `Agents/CloudSummaryAgent.md` | Extract and normalise scoring data from finding files for roll-up into summaries |
| `Scripts/get_cwd.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Helper to resolve and print the current working directory (used by agents to orient themselves) |
| `Scripts/scan_findings_files.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Scan `Output/Findings/` for all finding `.md` files and return structured list |
| `Scripts/scan_knowledge_refinement.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Identify open questions / unknowns in `Output/Knowledge/` files for agent refinement loop |
| `Scripts/triage_queue.py` | `Agents/Instructions.md`, `SessionKickoff.md` | Manage a prioritised queue of items to triage; used by session kickoff to present next item |
| `Scripts/analyze_experiment.py` | `Agents/Instructions.md` | Analyse experiment results and produce comparison metrics (see also `Agents/ExperimentAgent.md`) |
| `Scripts/learning_db.py` | `SessionKickoff.md` | Learning database helpers; partially superseded by `Scripts/Enrich/cozo_helpers.py` — confirm if still needed or fully superseded |
| `Scripts/regen_all.py` | `README.md` | Regenerate all Summary outputs from existing findings for a given provider (`--provider azure\|aws\|gcp`) |

---

## Notes

- `Scripts/learning_db.py` is also imported (with `try/except`) in
  `Scripts/Experiments/triage_experiment.py` — if it remains missing, that import
  silently fails. Either implement it or remove the import.
- `Scripts/clear_session.py` is one of the most-referenced missing scripts and blocks
  the session reset workflow described in `Agents/Instructions.md`.
- Priority order for implementation: `clear_session.py` → `scan_findings_files.py` →
  `triage_queue.py` → `extract_finding_scores.py` → remaining.
