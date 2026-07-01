# 🟣 Session Kick-off

When `sessionkickoff` starts, do this:

1. Read `AGENTS.md` and `Agents/Instructions.md`.
2. Check experiment state with `python3 Scripts/Experiments/triage_experiment.py resume`.
3. Scan the workspace with `python3 Scripts/Scan/scan_workspace.py --skip-repos`.
4. Check `Output/Knowledge/` for open questions.
5. Present the next triage choice to the user.

## Helper scripts
- `python3 Scripts/Scan/scan_workspace.py`
- `python3 Scripts/Experiments/triage_experiment.py resume`
- `python3 Scripts/Experiments/triage_experiment.py status`
- `python3 Scripts/Experiments/triage_experiment.py list`
- `python3 Scripts/Experiments/triage_experiment.py promote <id>`
- `python3 Scripts/scan_knowledge_refinement.py`
- `python3 Scripts/scan_findings_files.py`
- `python3 Scripts/scan_intake_files.py <Intake/Subfolder>`
- `python3 Scripts/Utils/compare_intake_to_findings.py --intake <path> --findings <path>`
- `python3 Scripts/get_cwd.py`

## Notes
- Use `Intake/` for bulk paths.
- Keep `Templates/Workflows.md` for menu/navigation detail.
- Keep detailed operating rules in `Agents/Instructions.md`.
