# 🟣 Session Kick-off

## Purpose
This note provides a simple prompt you can paste at the start of a new session so
the agent loads the repository instructions before doing any work.

## Prompt
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Knowledge/ and existing Findings/ for missing context.
Before asking any cloud-provider questions, first ask me what we are triaging (Cloud / Code / Repo scan).
- If Cloud: ask which provider (Azure/AWS/GCP) and then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan: ask for the repo path (or confirm current repo), language/ecosystem, and the scanner/source (e.g., SAST, dependency, secrets), then proceed without assuming cloud.
```
