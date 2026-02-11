# 🟣 Session Kick-off

## Purpose
This note provides a simple prompt you can paste at the start of a new session so
the agent loads the repository instructions before doing any work.

If the user types `sessionkickoff`, the agent should treat it as “run this kickoff”, check whether there are outstanding questions in `Knowledge/` (e.g., `## Unknowns` / `## ❓ Open Questions`), prompt the user to resume those if desired, and then ask what to triage next (single issue vs bulk `Intake/` path vs importing sample findings).

## Prompt
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Knowledge/ and existing Findings/ for missing context.
First, ask me to:
- **copy/paste a single issue** to triage, or
- **provide a path under `Intake/`** to process in bulk, or
- **import and triage the sample findings**.

- Example bulk paths in this repo:
  - `Intake/Cloud` (your cloud findings)
  - `Intake/Code` (your code findings)
  - `Intake/Sample/Cloud` (already-imported samples)
  - `Intake/Sample/Code` (already-imported samples)
  - `Sample Findings/Cloud` (import these samples, then triage)
  - `Sample Findings/Code` (import these samples, then triage)
Before asking any cloud-provider questions:
- If the user provided a bulk folder path that clearly implies scope (e.g., `Intake/Cloud` or `Intake/Code`), treat that as the triage type.
- Otherwise, ask what we are triaging (Cloud / Code / Repo scan).
- If Cloud: infer provider when the folder name implies it (e.g., `Intake/Sample/Cloud` = Azure samples in this repo); otherwise ask which provider (Azure/AWS/GCP) and then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan: ask for the repo path (or confirm current repo), language/ecosystem, and the scanner/source (e.g., SAST, dependency, secrets), then proceed without assuming cloud.

When asking **multiple-choice** questions, always include a **“Don’t know”** option.

As each kickoff question is answered, check whether it adds new context vs existing `Knowledge/`.
- If it’s new: record it **immediately** in `Knowledge/` as **Confirmed** (with timestamp).
- If it’s already captured: don’t duplicate.

When processing sample findings in bulk, process them sequentially and **auto-continue** to
next item. Use a default priority order unless I override it: (1) internet exposure,
(2) data stores/secrets, (3) identity/privilege, (4) logging/monitoring, (5) baseline
hardening. Only pause for questions that change scoring/applicability/scope.
- If a finding title clearly names a cloud service (e.g., *Storage account*, *Azure SQL*, *ACR*, *Key Vault*), record that service as **Confirmed in use** in `Knowledge/<Provider>.md`.

If you have title-only exports in `Intake/` and want to save tokens/time, you may generate draft
findings in bulk (then refine them one-by-one). Ask the user first because it writes files:
- `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <intake-path> --out-dir Findings/Cloud --update-knowledge`
  - Also generates: `Summary/Cloud/*.md` per-service summaries and `Summary/Risk Register.xlsx` (and logs to `Audit/`).

If Cloud + provider is confirmed, immediately create/update:
- `Knowledge/<Provider>.md`
- `Summary/Cloud/Architecture_<Provider>.md`

During triage, capture inferred environment context into Knowledge/ as explicit ASSUMPTIONS and ask me to confirm/deny.
Do **not** create any new findings beyond the original imported list; keep new discoveries as Knowledge/ context and/or conditional score notes inside the *existing* findings.
After bulk triage (or whenever assumptions accumulate), ask service-specific follow-up questions **one at a time** (prefix with `❓`).
- Ask cross-cutting questions once (e.g., “Are Private Endpoints used anywhere?”) rather than repeating per-service.
- When I answer, update `Knowledge/` (promote to Confirmed or correct/remove) and append an `Audit/` entry that records **the question + the answer** (including “Don’t know”).
Whenever `Knowledge/` is created or updated, generate/update the relevant architecture diagram under `Summary/Cloud/` (assumptions = dotted border).
```
