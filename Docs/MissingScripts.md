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
| `Scripts/clear_session.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `README.md` | Delete per-session artifacts under `Output/Findings/`, `Output/Knowledge/`, `Output/Summary/` | **stub created** |
| `Scripts/extract_finding_scores.py` | `Agents/Instructions.md`, `Agents/CodeSummaryAgent.md`, `Agents/RiskRegisterAgent.md`, `Agents/CloudSummaryAgent.md` | Extract and normalise scoring data from finding files for roll-up into summaries | |
| `Scripts/get_cwd.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Helper to resolve and print the current working directory (used by agents to orient themselves) | **stub created** |
| `Scripts/scan_findings_files.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Scan `Output/Findings/` for all finding `.md` files and return structured list | **stub created** |
| `Scripts/scan_knowledge_refinement.py` | `Agents/Instructions.md`, `Agents/RiskRegisterAgent.md`, `SessionKickoff.md` | Identify open questions / unknowns in `Output/Knowledge/` files for agent refinement loop | |
| `Scripts/triage_queue.py` | `Agents/Instructions.md`, `SessionKickoff.md` | Manage a prioritised queue of items to triage; used by session kickoff to present next item | |
| `Scripts/analyze_experiment.py` | `Agents/Instructions.md` | Analyse experiment results and produce comparison metrics (see also `Agents/ExperimentAgent.md`) | |
| `Scripts/learning_db.py` | `SessionKickoff.md` | **Superseded** by `Scripts/Persist/learning_db.py`. The `try/except` import in `Scripts/Experiments/triage_experiment.py` is intentional — if the root-level stub is absent, the Persist version is used as fallback. No action needed. | resolved |
| `Scripts/regen_all.py` | ~~`README.md`~~ | Regenerate all Summary outputs from existing findings for a given provider. Reference removed from README. | |
| `Agents/RepoSummaryAgent.md` | `AGENTS.md` | Agent instructions for generating executive repo summaries | |
| `Knowledge/DevSkeptic.md` | `AGENTS.md` | Reusable dev-centric context for DevSkeptic agent | |
| `Knowledge/PlatformSkeptic.md` | `AGENTS.md` | Reusable platform-centric context for PlatformSkeptic agent | |

---

## Notes

- `Scripts/learning_db.py` — **Resolved:** superseded by `Scripts/Persist/learning_db.py`. The silent `try/except` import in `Scripts/Experiments/triage_experiment.py` is intentional fallback behaviour.
- `Scripts/clear_session.py` is one of the most-referenced missing scripts and blocks
  the session reset workflow described in `Agents/Instructions.md`. **Stub created.**
- Priority order for remaining implementation: `triage_queue.py` → `extract_finding_scores.py` → `scan_knowledge_refinement.py` → `analyze_experiment.py`.
- `Agents/RepoSummaryAgent.md` and the `Knowledge/` files are also missing — create them when defining per-org conventions.
