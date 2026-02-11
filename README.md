# 🦖 Triage-Saurus

Read `AGENTS.md` first for repository-specific agent instructions.
To initialise a session, copy and paste this prompt:
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Knowledge/ and existing Findings/ for missing context.

First, check whether `Knowledge/` contains outstanding items under `## Unknowns` and/or `## ❓ Open Questions` (treat these as **refinement questions** in the UI).
- If yes: ask whether to **resume answering those now** (or proceed to new triage).

Then ask me to either **copy/paste the issue** to triage or **provide a path under `Intake/`** to process in bulk.
- Example bulk paths in this repo:
  - `Intake/Cloud` (your cloud findings)
  - `Intake/Code` (your code findings)
  - `Intake/Sample/Cloud` (copy from `Sample Findings/Cloud` first)
  - `Intake/Sample/Code` (copy from `Sample Findings/Code` first)
Before asking any cloud-provider questions:
- If the user provided a bulk folder path that clearly implies scope (e.g., `Intake/Cloud` or `Intake/Code`), treat that as the triage type.
- Otherwise, ask what we are triaging (Cloud / Code / Repo scan).
- If Cloud: infer provider when the folder name implies it (e.g., `Intake/Sample/Cloud` = Azure samples in this repo); otherwise ask which provider (Azure/AWS/GCP) and then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan:
  - First check `Knowledge/Repos.md` for known repo root path(s). If none, ask the user for their root repos folder.
  - Keep track of scanned repos in `Knowledge/Repos.md`; if the same repo is requested again, ask the user to confirm re-scan vs reuse.
  - Ask for the repo path (or confirm current repo), language/ecosystem, and the scanner/source/scope (SAST / dependency (SCA) / secrets / IaC / **All**).
  - Log repo scans under `Audit/` and output one consolidated finding per repo under `Findings/Repo/`.
  - Promote reusable context from repo scan (e.g., Terraform/IaC patterns) into `Knowledge/` as Confirmed/Assumptions to support cloud triage.

As each kickoff question is answered, check whether it adds new context vs existing `Knowledge/`.
- If it’s new: record it in `Knowledge/` as **Confirmed** (with timestamp).
- If it’s already captured: don’t duplicate.

After bulk triage (or whenever assumptions accumulate), ask follow-up assumption-confirmation questions **one at a time** (prefix with `❓`).
- Ask cross-cutting questions once (e.g., Private Endpoints used anywhere) rather than repeating per-service.
- On answer: update `Knowledge/` and append an `Audit/` entry.
```
The same prompt is also saved in `SessionKickoff.md`.

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
4. After any finding changes, the agent may regenerate
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
  - `Skills/clear_session.py` (delete per-session artifacts under `Findings/`, `Knowledge/`, `Summary/`)
- **Dependencies:** Uses only the Python standard library; no extra packages required.
  - Optional helper: `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
