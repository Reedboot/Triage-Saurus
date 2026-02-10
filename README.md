# Triage-Saurus

Read `AGENTS.md` first for repository-specific agent instructions.
To initialise a session, copy and paste this prompt:
```text
Initialise: read AGENTS.md and Agents/Instructions.md, then prompt me per repo instructions.
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
3. The agent creates or updates findings in `Findings/`, updates `Knowledge/`,
   and refreshes relevant summaries in `Summary/`.
4. After any finding changes, the agent regenerates
   `Summary/Risk Register.xlsx` using `Skills/risk_register.py`.

## Using Copilot CLI
1. Open a Copilot CLI session in the repository root.
2. Paste the prompt from `SessionKickoff.md`.
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Using Codex CLI
1. Open a Codex CLI session in the repository root.
2. Paste the prompt from `SessionKickoff.md`.
3. Follow the repository instructions in `AGENTS.md` and `Agents/Instructions.md`.

## Process sample findings

1. Use your chosen CLI
2. Enter the prompt `Triage review. Cloud provider: Azure. Process sample findings one by one
  without confirmations`.

**Tools**
- **Python:** `python3` is required to run `Skills/risk_register.py` for generating
  `Summary/Risk Register.xlsx`.
- **Dependencies:** Uses only the Python standard library; no extra packages required.
