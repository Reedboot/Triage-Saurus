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
- Ask one targeted question at a time; avoid bundling multiple confirmations into a single prompt.
- When kickoff questions are answered (triage type, cloud provider, repo path, scanner/source), check whether the answer adds new context vs existing `Knowledge/`.
  - If new: append it **immediately** to `Knowledge/` as **Confirmed** with a timestamp.
  - If already captured: don’t duplicate.
  - If Cloud + provider is confirmed: immediately update `Summary/Cloud/Architecture_<Provider>.md`.
- Prefer confirmed facts, **but capture inferred context** in `Knowledge/` as an
  explicit **assumption** and then ask the user to confirm/deny.
- When a finding implies additional environment context (e.g., “Defender for Cloud” recommendations imply Defender is enabled), record it in `Knowledge/` as an **assumption** and immediately ask the user to confirm/deny.
- When findings reference a specific cloud service as the **subject** of the finding (e.g., AKS, Key Vault, Storage Accounts), you may record that service as **Confirmed in use** in `Knowledge/` without asking (the finding itself implies the service exists).
- If a finding recommends enabling an **additional** service/control (e.g., DDoS Standard, Defender plan, Private Link), record that additional service/control as an **Assumption** until the user confirms.
- When processing findings in bulk (including sample findings), still update
  `Knowledge/` with inferred services/controls as **assumptions**, then ask the user
  to verify the assumptions as a follow-up step.
- Keep findings actionable: impact, exploitability, and concrete remediation.
- When a finding is created or updated, **immediately** update `Knowledge/` with any
  new inferred or confirmed facts discovered while writing the finding.
  - Capture inferred facts as **assumptions** and ask the user to confirm/deny.
  - Prefer reusable environment knowledge (services in use, guardrails, identity
    model, network defaults, dependencies) over one-off resource IDs.
- When `Knowledge/` is created or updated (including assumptions), **immediately**
  generate or update the provider architecture diagram under `Summary/Cloud/` (e.g.,
  `Summary/Cloud/Architecture_Azure.md`) to reflect the current known state and
  include any newly discovered services.
  - This is a **standing rule throughout the session** (do not wait until session
    kickoff or the end of triage).
  - **Confirmed services:** solid border.
  - **Assumed/inferred services:** dotted border.
- While writing/updating cloud findings, scan the finding content for implied **cloud services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
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
