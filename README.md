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

## Offline Pipeline (Phases 1–3, No LLM)

Run the full detection-to-output pipeline against any repo with a single command — no API keys, no internet, no LLM required.

```bash
python3 Scripts/run_pipeline.py --repo /path/to/repo
```

This creates a new experiment and runs:

| Phase | Script | What it does |
|---|---|---|
| **1** | `triage_experiment.py run` | opengrep detection rules → identify services → targeted misconfig scan → findings in DB |
| **2** | `discover_code_context.py` | Parses manifests, Dockerfiles, K8s YAML, detects languages/frameworks/RBAC → metadata in DB |
| **3a** | `render_finding.py` | Renders one MD per finding from DB → `Findings/<repo>/finding_<id>.md` |
| **3b** | `generate_diagram.py` | Generates layered architecture diagram → `Summary/Cloud/Architecture_AWS.md` |

### Output

```
Output/Learning/experiments/<id>_<name>/
├── Summary/
│   ├── Cloud/Architecture_AWS.md      ← layered architecture diagram
│   └── Repos/<repo-name>.md           ← languages, frameworks, K8s context
└── Findings/<repo-name>/
    └── finding_<id>.md                ← one MD per finding
```

### Options

| Flag | Purpose |
|---|---|
| `--name <name>` | Experiment name suffix (default: `offline_scan`) |
| `--experiment <id>` | Reuse an existing experiment instead of creating a new one |
| `--skip-phase1` | Skip Phase 1 (findings already in DB) |
| `--skip-phase2` | Skip Phase 2 (metadata already populated) |
| `--no-opengrep` | Phase 2 file parsing only (no opengrep detection rules) |

### Next steps after Phase 1–3

Phases 4–6 require an LLM:

```bash
python3 Scripts/enrich_findings.py --experiment <id>          # Phase 4 — LLM titles/descriptions/severity
python3 Scripts/run_skeptics.py --experiment <id> --reviewer all  # Phase 5 — DevSkeptic/PlatformSkeptic
python3 Scripts/triage_experiment.py complete <id>            # Mark done
```

Inspect DB-first topology relationships for a resource:

```bash
python3 Scripts/query_resource_graph.py --experiment <id> --resource <resource_name> --query all
```

## Cozo ingestion pipeline

Once Phase 1 has generated opengrep JSON output, `Scripts/store_opengrep_for_cozo.py` can persist the raw findings, metadata, and metavars into a Cozo embedded database for additional enrichment or rule-based traversal. The script records the originating repo, source file paths, line numbers, rule IDs, severity, and all metadata (category, technology, provider hints) along with each metavariable so downstream agents can derive attributes and relationships.

```bash
python3 Scripts/store_opengrep_for_cozo.py scan_<repo>.json --repo my-repo [--repo-path /path/to/repo]
```

By default the database lives under `Output/Data/cozo.db` (directory created automatically) but you can override the path with `--cozo-db`. Run the script before or after the existing SQLite pipeline so that Cozo contains the same structured detection data that future rules will enrich or relate to other resources.

To simplify batch scans, `Scripts/run_cozo_repos.sh` reads `Intake/ReposToScan.txt`, runs opengrep for every listed repo, imports the JSON result into `Output/Data/cozo.db`, prints the resources detected per repo, and now automatically invokes `Scripts/generate_repo_summary_from_cozo.py` so every scan also emits a repo-level Markdown summary under `Output/Summary/Repos/`. The script tracks recent scans via the Cozo `repo_scans` table (skip within one hour unless you pass `--force`), writes a timestamped audit log under `Output/Audit/CozoScan_<timestamp>.md`, and continues to surface detected providers/lines in the console for quick review.

Use `Scripts/generate_repo_summary_from_cozo.py --repo <repo> --scan-id <scan-id> --output-dir Output/Summary/Repos` directly if you only need to regenerate the report for a particular scan or re-run summaries after editing templates.

---

## Security Rules Library

Triage-Saurus now includes a **declarative security rules library** in opengrep/Semgrep-compatible format:

### What's Included
- **50+ production-ready rules** covering:
  - **Terraform/Azure IaC** (33 rules): Secrets, network security, access control, logging, encryption
  - **Kubernetes** (17 rules): Container security, RBAC, network policies, privileged access
  - **Secret Detection** (2 rules): AWS keys, SQL connection strings

### Rules as Single Source of Truth
- **Rules folder**: `Rules/Misconfigurations/` and `Rules/Misconfigurations/Secrets/` — declarative WHAT to check
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

**Primary (Mandatory) Detection – opengrep**
```bash
opengrep scan --config Rules/ /path/to/repo
```
- Run this command for every IaC/code scan whenever opengrep is installed (default state).
- Capture the command, timestamp, and target path in the session audit log.

**Fallback (only if opengrep unavailable)**
```bash
# Temporary manual grep until opengrep is restored
grep -r "pattern" --include="*.tf"
```
- Document the outage and remediation plan in the audit log, then rerun opengrep ASAP.

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

**Required tools**
- **Python 3.8+** — Required to run all scripts and helpers:
  - `Scripts/risk_register.py` (generate `Output/Summary/Risk Register.xlsx`)
  - `Scripts/regen_all.py --provider <azure|aws|gcp>` (regenerate Summary outputs from existing findings)
  - `Scripts/validate_findings.py` (validate finding + summary formatting)
  - `Scripts/clear_session.py` (delete per-session artifacts under `Output/Findings/`, `Output/Knowledge/`, `Output/Summary/`)
- **SQLite 3** — Required (used by the pipeline and accessible from Python):
  - Database location: `Output/Learning/triage.db`
  - Initialize the DB: `python3 Scripts/init_database.py`
  - CLI (optional but useful): `sqlite3` (Debian/Ubuntu: `sudo apt update && sudo apt install -y sqlite3`; macOS Homebrew: `brew install sqlite`)
- **opengrep** — REQUIRED for Phase 1 detection rules (preferred engine):
  - Used by: `opengrep scan --config Rules/ /path/to/repo`
  - Ensure `opengrep` is installed and on PATH. If `opengrep` is not available, the system falls back to manual grep (document the outage and re-run with `opengrep` as soon as possible).
- `pycozo` + `cozo-embedded` (pip) — Install via `python3 -m pip install pycozo cozo-embedded==0.7.6` so `Scripts/store_opengrep_for_cozo.py` can write detections into an embedded Cozo database.
- `jinja2` (pip) — Install via `python3 -m pip install jinja2` so the finding renderer can populate the Markdown templates in `Templates/`.
- **git** — recommended for repository metadata and repo discovery (used by Scripts/pull_repo.py and DB repo registration).
- **Optional / Helpers**:
  - `pysqlite3-binary` (pip) — if a system sqlite3 CLI is not present but Python access to SQLite is required: `pip install pysqlite3-binary`
  - Other standard Unix tooling: `grep`, `awk`, `sed`, `python3`, etc.

Notes:
- The repository and scripts are designed to work with the Python standard library where possible; third-party binaries listed above (opengrep, sqlite3 CLI) are required for full functionality and for parity with experiments and rule-based scans.
- See `Rules/README.md` for details about the rules engine and `Docs/DatabaseSchema.md` for DB layout.

## Auto-regenerate risk register
- Run `python3 Scripts/watch_risk_register.py` in a separate terminal to regenerate `Output/Summary/Risk Register.xlsx` whenever `Output/Findings/**/*.md` changes.
- Use `python3 Scripts/watch_risk_register.py --full` to also refresh summaries/descriptions/scores before regenerating.
