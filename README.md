# 🦖 Triage-Saurus

Security triage workspace for repo scans, findings, knowledge capture, and risk register updates.

## Start here
- Read `AGENTS.md`, then `Agents/Instructions.md`.
- In the web UI, trigger `sessionkickoff` or paste `SessionKickoff.md`.
- Provide either a single issue or a bulk path under `Intake/`.

## Main workflows
- **Session bootstrap:** `SessionKickoff.md`
- **Repo/context discovery:** `Agents/ContextDiscoveryAgent.md` and `Agents/RepoAgent.md`
- **Security review:** `Agents/SecurityAgent.md`
- **Cloud diagrams:** `Agents/ArchitectureAgent.md`
- **Route tracing:** `.github/skills/route-trace/SKILL.md`
- **Learning/experiments:** `Agents/ExperimentAgent.md` and `Agents/LearningAgent.md`

## Core commands
- `python3 Scripts/Utils/run_pipeline.py --repo /path/to/repo`
- `python3 Scripts/Scan/scan_workspace.py`
- `python3 Scripts/Experiments/triage_experiment.py resume`
- `python3 Scripts/Experiments/triage_experiment.py status`
- `python3 Scripts/Experiments/triage_experiment.py list`

## Output
The repo writes findings, knowledge, summaries, and learning artefacts under `Output/`.

## Notes
- `opengrep scan --config Rules/ <target>` is the primary detection path when available.
- `node_modules/`, `.venv/`, `dist/`, `build/`, and similar generated folders are excluded from normal scans.
