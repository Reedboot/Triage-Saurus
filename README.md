# Triage-Saurus

Read `AGENTS.md` first for repository-specific agent instructions.
To initialise a session, copy and paste this prompt:
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Knowledge/ and existing Findings/ for missing context.
Before asking any cloud-provider questions, first ask me what we are triaging (Cloud / Code / Repo scan).
- If Cloud: ask which provider (Azure/AWS/GCP) and then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan: ask for the repo path (or confirm current repo), language/ecosystem, and the scanner/source (e.g., SAST, dependency, secrets), then proceed without assuming cloud.
```
The same prompt is also saved in `SessionKickoff.md`.

Author: Neil Reed 06/02/2026

## Purpose
This repository supports AI CLI tooling (e.g., Copilot CLI, Codex CLI) to run
consistent security triaging. It provides agent instructions, templates, and
workflows for analysing scanner findings, updating knowledge, producing
findings, maintaining summaries, and regenerating the risk register.

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
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Using Codex CLI
1. Open a Codex CLI session in the repository root.
2. Type `sessionkickoff` (or paste the prompt from `SessionKickoff.md`).
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Process sample findings

1. Use your chosen CLI
2. Enter the prompt `Triage review. Cloud provider: Azure. Process sample findings one by one
  without confirmations`.

**Tools**
- **Python:** `python3` is required to run:
  - `Skills/risk_register.py` (generate `Summary/Risk Register.xlsx`)
  - `Skills/clear_session.py` (delete per-session artifacts under `Findings/`, `Knowledge/`, `Summary/`)
- **Dependencies:** Uses only the Python standard library; no extra packages required.
