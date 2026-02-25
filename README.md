# 🦖 Triage-Saurus

Read `AGENTS.md` first for repository-specific agent instructions.

## Session kick-off
- In your CLI, type `sessionkickoff`, **or** copy/paste the canonical prompt from [`SessionKickoff.md`](SessionKickoff.md).
- Then provide either a single issue to triage (paste into chat) or a bulk path under `Intake/`.
- Bulk import note: `Scripts/generate_findings_from_titles.py` now skips duplicate titles to avoid duplicate findings/risk register rows.

## Purpose
This repository supports AI CLI tooling (e.g., Copilot CLI, Codex CLI) to run
consistent security triaging. It provides agent instructions, templates, and
workflows for analysing scanner findings, updating knowledge, producing
findings, maintaining summaries, and regenerating the risk register.

## Experiment Mode (Self-Optimizing Triage)

Triage-Saurus can run in **experiment mode** to optimize its own effectiveness:

1. **Run baseline**: `triage experiment run` — scans repos with current strategy
2. **Human review**: `triage experiment review <id>` — mark findings correct/wrong/missed
3. **Learn**: `triage experiment learn <id>` — system proposes improvements
4. **Repeat**: System runs optimized experiments until convergence (<5% improvement)

### Key Commands
| Command | Purpose |
|---------|---------|
| `triage resume` | Continue from where last session left off |
| `triage experiment list` | Show all experiments with metrics |
| `triage experiment compare <id1> <id2>` | Compare two runs side-by-side |
| `triage experiment status` | Show current state |

### What Gets Optimized
- **Scan order**: Which scans yield highest-value findings
- **Question order**: Which questions impact scores most
- **File patterns**: Which paths contain security-relevant code
- **Agent instructions**: Per-experiment tweaks (isolated per run)

### Cross-Session Continuity
State is stored in `Output/Learning/state.json` — any agent can resume from where the previous session left off. Experiment metrics are stored in SQLite for efficient querying.

See `Agents/ExperimentAgent.md` and `Agents/LearningAgent.md` for full details.

---

## Security Rules Library

Triage-Saurus now includes a **declarative security rules library** in opengrep/Semgrep-compatible format:

### What's Included
- **50+ production-ready rules** covering:
  - **Terraform/Azure IaC** (33 rules): Secrets, network security, access control, logging, encryption
  - **Kubernetes** (17 rules): Container security, RBAC, network policies, privileged access
  - **Secret Detection** (2 rules): AWS keys, SQL connection strings

### Rules as Single Source of Truth
- **Rules folder**: `Rules/IaC/` and `Rules/Secrets/` — declarative WHAT to check
- **Scripts**: Read rules and execute checks — imperative HOW
- **Findings**: Track which rule detected them via `detected_by_rule` field
- **Learning**: Create new rules when detection gaps identified

### Key Features
- ✅ **Opengrep/Semgrep compatible** — Industry standard format
- ✅ **Comprehensive metadata** — CWE, severity, technology tags, Five Pillars mapping
- ✅ **Measurable** — Track detection rate per rule in experiments
- ✅ **Continuously improving** — LearningAgent creates rules for missed detections

### Documentation
- `Rules/README.md` — Overview and usage
- `Rules/Summary.md` — Complete catalog of all rules
- `Rules/CreationGuide.md` — How to create new rules
- `Agents/Instructions.md` — When and how to create rules
- Finding templates — Require rule tracking for attribution

### Usage

**Manual Detection** (current):
```bash
# Each rule includes pattern examples for manual grep
grep -r "nonsensitive(" --include="*.tf"
```

**Automated Scanning** (future):
```bash
# When opengrep installed
opengrep scan --config Rules/ /path/to/repo
```

**Learning from Gaps**:
When experiments or external tools find issues we miss → LearningAgent creates rules → Track effectiveness in next run

See `Rules/` folder for complete library and documentation.

---

## License
See `LICENSE` (non-commercial internal use; no redistribution; no warranty).

Author: Neil Reed — <https://www.linkedin.com/in/reedneil>

## Workflow Overview
1. Start a CLI session in the repo root and paste the prompt from
   `SessionKickoff.md`.
2. Provide a scanner issue to triage; the agent will confirm cloud provider or
   context as needed.
3. The agent creates or updates findings in `Output/Findings/`, updates `Output/Knowledge/`—the live repository of environment, services, and dependency facts used to fill missing context—and refreshes relevant summaries in `Output/Summary/`.
4. After any finding changes, the agent **regenerates**
   `Output/Summary/Risk Register.xlsx` using `Scripts/risk_register.py`.
   - This spreadsheet is **ExCo/CISO-facing** (no team/status columns); priority/ranking is deterministic from finding scores.

> Note: By default, artifacts under `Output/Findings/`, `Output/Knowledge/`, and `Output/Summary/`
> are **gitignored** so they remain **user-owned**. If you want to persist/share
> them via git, update `.gitignore` intentionally.

## Using Copilot CLI
1. Open a Copilot CLI session in the repository root.
2. Type `sessionkickoff` (or paste the prompt from `SessionKickoff.md`).
   - The agent should first check for outstanding items under `Knowledge/` → `## Unknowns` / `## ❓ Open Questions` (present these as **refinement questions** in the UI) and offer to resume those.
   - If there are no refinement questions and `Knowledge/` is empty (first run), it should greet with: `🦖 Welcome to Triage-Saurus.`
   - Then it should ask what to triage next (single issue vs bulk `Intake/` path).
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Using Codex CLI
1. Open a Codex CLI session in the repository root.
2. Type `sessionkickoff` (or paste the prompt from `SessionKickoff.md`).
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Process sample findings

1. Stage samples into `Intake/`:
   - `python3 Scripts/stage_sample_findings_to_intake.py --type cloud`
2. Use your chosen CLI
3. Provide the bulk path `Intake/Sample/Cloud` (or `Intake/Sample/Code`) for processing.

During bulk processing, if a finding title clearly names a cloud service (e.g., *Storage account*, *Azure SQL*, *ACR*, *Key Vault*), record that service as **Confirmed in use** in `Knowledge/<Provider>.md`.

## Answering questions quickly
- When the agent asks a multiple-choice question in plain chat, it should provide **numbered options** so you can reply with just `1`, `2`, etc.

**Tools & Requirements**
- **Python 3.8+:** Required to run all scripts
  - `Scripts/risk_register.py` (generate `Output/Summary/Risk Register.xlsx`)
  - `Scripts/regen_all.py --provider <azure|aws|gcp>` (regenerate Summary outputs from existing findings)
  - `Scripts/validate_findings.py` (validate finding + summary formatting)
  - `Scripts/clear_session.py` (delete per-session artifacts under `Output/Findings/`, `Output/Knowledge/`, `Output/Summary/`)
- **SQLite 3:** Built into Python standard library
  - Database location: `Output/Learning/triage.db`
  - Stores: experiments, resources, connections, findings, properties, context Q&A, knowledge facts
  - Initialize: `python3 Scripts/init_database.py`
  - See `Docs/DatabaseSchema.md` for table details
- **Dependencies:** Uses only the Python standard library; no extra packages required.
  - Optional helper: `python3 Scripts/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`

## Auto-regenerate risk register
- Run `python3 Scripts/watch_risk_register.py` in a separate terminal to regenerate `Output/Summary/Risk Register.xlsx` whenever `Output/Findings/**/*.md` changes.
- Use `python3 Scripts/watch_risk_register.py --full` to also refresh summaries/descriptions/scores before regenerating.
