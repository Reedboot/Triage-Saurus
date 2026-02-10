# 🟣 Agent Instructions

## Purpose
This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Findings/` using the relevant template.
3. Capture confirmed facts under `Knowledge/` (this is the living, authoritative log used to resolve missing context—add new services, dependencies, configuration artefacts, or compound problems there when they are discovered and link to them from related findings). Examples include which terraform modules are in use and their parameters, allowed IP blocks, whether managed identities are enabled, which cloud providers are active, and what CI/CD pipeline(s) deploy the services.
4. Update `Summary/` outputs (cloud resource summaries and risk register).

## Behaviour
- Follow `Settings/Styling.md` for formatting rules.
- Prefer confirmed facts over assumptions; call out gaps explicitly.
- Keep findings actionable: impact, exploitability, and concrete remediation.
- When a new finding overlaps an existing one, link them under **Compounding Findings**.

## Outputs
- **Cloud findings:** `Findings/Cloud/<Titlecase>.md`
- **Code findings:** `Findings/Code/<Titlecase>.md`
- **Cloud summaries:** `Summary/Cloud/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Skills/risk_register.py`

## After changes to findings
- Regenerate the risk register:
  - `python3 Skills/risk_register.py`
- Ensure each finding includes:
  - `- **Overall Score:** <severity> <n>/10`
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
