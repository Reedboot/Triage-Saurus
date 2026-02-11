# 🟣 Session Kick-off

## Purpose
This note provides a simple prompt you can paste at the start of a new session so
the agent loads the repository instructions before doing any work.

## Prompt
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Knowledge/ and existing Findings/ for missing context.
First, ask me to either **copy/paste the issue** to triage or **provide a path to a folder of findings** to process in bulk.
- Example bulk paths in this repo:
  - `Sample Findings/Cloud` (Azure cloud sample findings)
  - `Sample Findings/Code` (code sample findings)
Before asking any cloud-provider questions, ask what we are triaging (Cloud / Code / Repo scan).
- If Cloud: ask which provider (Azure/AWS/GCP) and then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan: ask for the repo path (or confirm current repo), language/ecosystem, and the scanner/source (e.g., SAST, dependency, secrets), then proceed without assuming cloud.

As each kickoff question is answered, check whether it adds new context vs existing `Knowledge/`.
- If it’s new: record it **immediately** in `Knowledge/` as **Confirmed** (with timestamp).
- If it’s already captured: don’t duplicate.

When processing sample findings in bulk, process them sequentially and **auto-continue** to
next item; only pause for questions that change scoring/applicability/scope.

If you have title-only exports and want to save tokens/time, you may generate draft
findings in bulk (then refine them one-by-one):
- `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> --update-knowledge`

If Cloud + provider is confirmed, immediately create/update:
- `Knowledge/<Provider>.md`
- `Summary/Cloud/Architecture_<Provider>.md`

During triage, capture inferred environment context into Knowledge/ as explicit ASSUMPTIONS and ask me to confirm/deny.
Whenever `Knowledge/` is created or updated, generate/update the relevant architecture diagram under `Summary/Cloud/` (assumptions = dotted border).
```
