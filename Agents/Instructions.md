# 🟣 Agent Instructions

## Purpose
This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Findings/` using the relevant template.
3. Capture confirmed facts under `Knowledge/` as a timestamped (DD/MM/YYYY HH:MM) append-only learned log (this is the living, authoritative record used to resolve missing context—add new services, dependencies, configuration artefacts, or compound problems there when they are discovered and link to them from related findings). Examples include which terraform modules are in use and their parameters, allowed IP blocks, whether managed identities are enabled, which cloud providers are active, and what CI/CD pipeline(s) deploy the services.
4. Update `Summary/` outputs (cloud resource summaries and risk register).

## Behaviour
- Follow `Settings/Styling.md` for formatting rules.
- At session start, quickly review existing `Knowledge/` and any existing findings under `Findings/` to spot missing context; ask targeted questions to fill gaps before proceeding.
- Prefer confirmed facts over assumptions; call out gaps explicitly.
- When a finding implies additional environment context (e.g., “Defender for Cloud” recommendations imply Defender is enabled), record it in `Knowledge/` as an **assumption** and immediately ask the user to confirm/deny.
- When findings reference specific Azure services (e.g., Storage Accounts, Key Vault, AKS), record the implied **service in use** in `Knowledge/` as an **assumption** and immediately ask the user to confirm/deny, stating the reasoning.
- Keep findings actionable: impact, exploitability, and concrete remediation.
- When new confirmed cloud services, access paths, or trust boundaries are added to `Knowledge/`, update the provider architecture diagram under `Summary/Cloud/` (e.g., `Summary/Cloud/Architecture_Azure.md`) to reflect the new confirmed components.
- While writing/updating cloud findings, scan the finding content for implied **Azure services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
- When a new finding overlaps an existing one, link them under **Compounding Findings**.
- **Avoid running git commands by default** (e.g., `git status`, `git diff`, `git restore`). Only use git when the user explicitly asks, and explain why it’s needed.
- **Avoid running scripts/automations by default**. If you propose running a script (including repo utilities like `python3 Skills/risk_register.py`), first explain:
  - what it does,
  - what files it will write/change,
  - why it’s necessary now.

## Outputs

- **Default behaviour:** outputs under `Findings/`, `Knowledge/`, and `Summary/` are
  **generated per-user/session and are intentionally untracked** (see `.gitignore`).
  Change that only if you explicitly want to commit triage artifacts.

- **Cloud findings:** `Findings/Cloud/<Titlecase>.md`
- **Code findings:** `Findings/Code/<Titlecase>.md`
- **Cloud summaries:** `Summary/Cloud/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Skills/risk_register.py`

## After changes to findings
- If you need an updated risk register, run:
  - `python3 Skills/risk_register.py`

## Utility scripts
- **Clear session artifacts (destructive):**
  - Dry-run: `python3 Skills/clear_session.py`
  - Delete: `python3 Skills/clear_session.py --yes`

- Ensure each finding includes:
  - `- **Overall Score:** <severity> <n>/10`
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
