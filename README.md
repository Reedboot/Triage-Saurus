# 🦖 Triage-Saurus

Read `AGENTS.md` first for repository-specific agent instructions.

## Session kick-off
- In your CLI, type `sessionkickoff`, **or** copy/paste the canonical prompt from [`SessionKickoff.md`](SessionKickoff.md).
- Then provide either a single issue to triage (paste into chat) or a bulk path under `Intake/`.
- Bulk import note: `Skills/generate_findings_from_titles.py` now skips duplicate titles to avoid duplicate findings/risk register rows.

## Purpose
This repository supports AI CLI tooling (e.g., Copilot CLI, Codex CLI) to run
consistent security triaging. It provides agent instructions, templates, and
workflows for analysing scanner findings, updating knowledge, producing
findings, maintaining summaries, and regenerating the risk register.

## License
See `LICENSE` (non-commercial internal use; no redistribution; no warranty).

Author: Neil Reed — <https://www.linkedin.com/in/reedneil>

## Workflow Overview
1. Start a CLI session in the repo root and paste the prompt from
   `SessionKickoff.md`.
2. Provide a scanner issue to triage; the agent will confirm cloud provider or
   context as needed.
3. The agent creates or updates findings in `Findings/`, updates `Knowledge/`—the live repository of environment, services, and dependency facts used to fill missing context—and refreshes relevant summaries in `Summary/`.
4. After any finding changes, the agent **regenerates**
   `Summary/Risk Register.xlsx` using `Skills/risk_register.py` (explain why before running).

> Note: By default, artifacts under `Findings/`, `Knowledge/`, and `Summary/`
> are **gitignored** so they remain **user-owned**. If you want to persist/share
> them via git, update `.gitignore` intentionally.

## Using Copilot CLI
1. Open a Copilot CLI session in the repository root.
2. Type `sessionkickoff` (or paste the prompt from `SessionKickoff.md`).
   - The agent should first check for outstanding items under `Knowledge/` → `## Unknowns` / `## ❓ Open Questions` (present these as **refinement questions** in the UI) and offer to resume those.
   - Then it should ask what to triage next (single issue vs bulk `Intake/` path).
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Using Codex CLI
1. Open a Codex CLI session in the repository root.
2. Type `sessionkickoff` (or paste the prompt from `SessionKickoff.md`).
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Process sample findings

1. Stage samples into `Intake/`:
   - `python3 Skills/stage_sample_findings_to_intake.py --type cloud`
2. Use your chosen CLI
3. Provide the bulk path `Intake/Sample/Cloud` (or `Intake/Sample/Code`) for processing.

During bulk processing, if a finding title clearly names a cloud service (e.g., *Storage account*, *Azure SQL*, *ACR*, *Key Vault*), record that service as **Confirmed in use** in `Knowledge/<Provider>.md`.

**Tools**
- **Python:** `python3` is required to run:
  - `Skills/risk_register.py` (generate `Summary/Risk Register.xlsx`)
  - `Skills/regen_all.py --provider <azure|aws|gcp>` (regenerate Summary outputs from existing findings)
  - `Skills/validate_findings.py` (validate finding + summary formatting)
  - `Skills/clear_session.py` (delete per-session artifacts under `Findings/`, `Knowledge/`, `Summary/`)
- **Dependencies:** Uses only the Python standard library; no extra packages required.
  - Optional helper: `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
