# 🟣 Session Kick-off

## Purpose
This note provides a simple prompt you can paste at the start of a new session so
the agent loads the repository instructions before doing any work.

If the user types `sessionkickoff`, the agent should treat it as “run this kickoff”, check whether there are outstanding questions in `Output/Knowledge/` (sections `## Unknowns` / `## ❓ Open Questions`) and refer to them as **refinement questions** in the UI, prompt the user to resume those if desired, and then ask what to triage next (single issue vs bulk `Intake/` path vs importing sample findings).

Note: `Output/Knowledge/` may store provider files at the top-level (e.g., `Output/Knowledge/Azure.md`) as well as subfolders.
To make this reliable across different CLIs/tooling, **do not rely on recursive glob patterns** like `Output/Knowledge/**/*.md`.

Prefer the consolidated workspace scan helper (stdout-only):
- `python3 Skills/scan_workspace.py`
It scans `Output/Knowledge/` (refinement questions), `Output/Findings/`, and common `Intake/`/sample paths.

Targeted helpers still exist (stdout-only):
- `python3 Skills/scan_knowledge_refinement.py`
- `python3 Skills/scan_findings_files.py`
- `python3 Skills/scan_intake_files.py <Intake/Subfolder>`

## Prompt
```text
Initialise: read AGENTS.md and Agents/Instructions.md. Then scan Output/Knowledge/ and existing Output/Findings/ for missing context.

First, check whether `Output/Knowledge/` contains outstanding items under `## Unknowns` and/or `## ❓ Open Questions` (treat these as **refinement questions** in the UI).
- If yes: ask whether to **resume answering those now** (or proceed to new triage).
- If no, and `Output/Knowledge/` is effectively empty (e.g., `scan_knowledge_refinement.py` reports `Knowledge markdown files: 0`), treat this as a **first run / fresh workspace** and start by saying:
  - `🦖 Welcome to Triage-Saurus.`

If there are **no existing findings** under `Output/Findings/`, treat this as a **new instance** (fresh workspace) and prioritise onboarding the first batch (single issue, bulk Intake, or sample import).

To make this reliable across different CLIs/tooling, avoid shell `find`/recursive globs and use the repo helper scripts (stdout-only):
- `python3 Skills/scan_workspace.py` (preferred)
- `python3 Skills/scan_findings_files.py` (targeted)

Then ask me to (numbered options):
1. **Copy/paste a single issue** to triage
2. **Provide a path under `Intake/`** to process in bulk
3. **Import and triage the sample findings**
4. **Scan a repo**

- If the user chooses option **2** (bulk intake), ask for the folder path using a **selectable** multiple-choice prompt (and allow freeform input for custom paths).
  - Do **not** include numeric prefixes in the choice labels; the UI will handle numbering/selection.
- Suggested bulk paths in this repo:
  - `Intake/Cloud` (your cloud findings)
  - `Intake/Code` (your code findings)
  - `Intake/Sample/Cloud` (already-imported samples, if present)
  - `Intake/Sample/Code` (already-imported samples, if present)
  - `Sample Findings/Cloud` (import these samples, then triage)
  - `Sample Findings/Code` (import these samples, then triage)
  - Before offering any of these as **selectable choices**, verify the folder actually contains triageable files by running (stdout-only):
    - `python3 Skills/scan_intake_files.py <candidate-path>`
  - Only offer **non-empty** candidate folders as choices; if none are non-empty, fall back to a freeform path prompt (or suggest importing samples first).
- **Multi-day / idempotent bulk processing:** before you start bulk triage on a chosen intake path, check whether items already exist under `Output/Findings/Cloud`.
  - Run (stdout-only): `python3 Skills/compare_intake_to_findings.py --intake <Intake/...> --findings Output/Findings/Cloud`
  - If duplicates are found, ask the user to confirm proceeding with **new items only** (recommended).
  - If there are **no new items**, stop and tell the user the intake has likely already been processed.
Before asking any cloud-provider questions:
- If the user provided a bulk folder path that clearly implies scope (e.g., `Intake/Cloud` or `Intake/Code`), treat that as the triage type.
- Otherwise, ask what we are triaging (Cloud / Code / Repo scan).
- If Cloud: infer provider when the folder name implies it (e.g., `Intake/Sample/Cloud` = Azure samples in this repo).
  - If the provider is not explicit from the folder, quickly skim the intake titles; if they strongly indicate a provider, **say why** (1 short clause) on its own line, then ask a single confirmation question prefixed with `❓` on the next line.
    - Example wording:
      - “🤔 From the item titles (Key Vault / Entra / Defender), this looks like **Azure**.”
      - “❓ Can you confirm the cloud provider?”
    - Choices should be provider names only: `Azure` / `AWS` / `GCP` / `Don’t know` (freeform allowed for other).
  - Then ask targeted context questions (services, environments, networks, pipelines, identities).
- If Code/Repo scan:
  - First check `Output/Knowledge/Repos.md` for known repo root path(s).
    - If it **does not exist** or has no repo roots recorded, **suggest a default** based on the current working directory.
      - Prefer using the stdout-only helper to avoid guesswork: `python3 Skills/get_cwd.py` (it prints `cwd` + `suggested_repos_root`).
      - Then ask: **"I don’t currently know the root directory for your repos — should I use `<suggested path>`?"** (include **Yes / No / Don’t know**).
    - If the user confirms or provides one, create/update `Output/Knowledge/Repos.md` and record the repo root path(s).
  - **Only after** at least one repo root is recorded (or the user explicitly confirms **"current repo"**), ask which repo/directory under that root should be scanned.
  - **Do not ask for language/ecosystem up-front** — infer languages/frameworks from repo contents (lockfiles, build files, manifests, imports) and record them in the repo finding.
  - Ask for the scanner/source/scope (SAST / dependency (SCA) / secrets / IaC / **All**).
  - **Repo selection input:** accept either:
    - a single repo name/path,
    - a list (comma/newline separated), or
    - a simple wildcard/prefix pattern like `terraform-*`.
    - If the user provides a pattern/wildcard, **expand it into concrete repo names** and ask for an explicit confirmation of the expanded list before scanning.
  - **Repo selection defaults (when many match):** prioritise scanning shared module repos first (e.g., `*-modules`), then “edge” networking/security repos (network, firewall, gateway/WAF, DDoS), then identity, then data stores, then app/service repos.
  - If the same repo is requested again, ask the user to confirm re-scan vs reuse.
  - Log repo scans under `Output/Audit/` and output one consolidated finding per repo under `Output/Findings/Repo/`.
  - During repo scans, extract:
    - cloud resources/services deployed or referenced (IaC + config),
    - service dependencies (DBs, queues, logs/telemetry, APIs) from connection strings/config,
    - module/dependency sources (e.g., Terraform modules, internal/shared company repos),
    - and container/Kubernetes signals (Skaffold/Helm/Dockerfiles).
      - For Dockerfiles, capture both the **dev/local image** and the **shipping/runtime base image** (often multi-stage builds with multiple `FROM` lines; the later stages are commonly the shipped service base).
  - It is OK to list dependencies/modules (including private/internal module repos). If a dependency/module points to another company repo, ask the user to provide that repo next for better context.
  - When you discover CI/CD (pipelines, runners, deploy scripts), it is OK to ask clarification questions about where secrets are stored and how CI/CD authenticates/connects to the target environment.
  - If you detect **Hiera** (YAML hierarchy/overrides), treat it as an environment-scope signal, but **do not** ask about environment tiers during the repo scan itself. Instead, record it in `Knowledge/` as an **Assumption** and defer any environment-scope questions until the user starts cloud triage (or explicitly requests environment scoping).
  - It’s OK to include code/config **evidence snippets** with **file path + line numbers** in the repo finding.
  - Promote reusable context from repo scan into `Output/Knowledge/` as Confirmed/Assumptions to support cloud triage.

When asking **multiple-choice** questions, always include a **“Don’t know”** option.

As each kickoff question is answered, check whether it adds new context vs existing `Output/Knowledge/`.
- If it’s new: record it **immediately** in `Output/Knowledge/` as **Confirmed** (with timestamp).
- If it’s already captured: don’t duplicate.

When processing sample findings in bulk, process them sequentially and **auto-continue** to
next item. Use a default priority order unless I override it: (1) internet exposure,
(2) data stores/secrets, (3) identity/privilege, (4) logging/monitoring, (5) baseline
hardening. Only pause for questions that change scoring/applicability/scope.
- If a finding title clearly names a cloud service (e.g., *Storage account*, *Azure SQL*, *ACR*, *Key Vault*), record that service as **Confirmed in use** in `Knowledge/<Provider>.md`.

If you have title-only exports in `Intake/` and want to save tokens/time, you may generate draft
findings in bulk (then refine them one-by-one). Ask the user first because it writes files:
- `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <intake-path> --out-dir Output/Findings/Cloud --update-knowledge`
  - Also generates: `Output/Summary/Cloud/*.md` per-service summaries and `Output/Summary/Risk Register.xlsx` (ExCo/CISO-facing; no team/status columns) (and logs to `Output/Audit/`).

If Cloud + provider is confirmed, immediately create/update:
- `Output/Knowledge/<Provider>.md`
- `Output/Summary/Cloud/Architecture_<Provider>.md`

During triage, capture inferred environment context into Knowledge/ as explicit ASSUMPTIONS and ask me to confirm/deny.
Do **not** create any new findings beyond the original imported list; keep new discoveries as Knowledge/ context and/or conditional score notes inside the *existing* findings.
After bulk triage (or whenever assumptions accumulate), ask service-specific follow-up questions **one at a time** (prefix with `❓`).
- Ask cross-cutting questions once (e.g., “Are Private Endpoints used anywhere?”) rather than repeating per-service.
- When I answer, update `Output/Knowledge/` (promote to Confirmed or correct/remove) and append an `Output/Audit/` entry that records **the question + the answer** (including “Don’t know”).
Whenever `Output/Knowledge/` is created or updated, generate/update the relevant architecture diagram under `Output/Summary/Cloud/` (assumptions = dotted border).
```

## See also
- Repo overview + workflow: `README.md`
- Canonical operating rules: `AGENTS.md` and `Agents/Instructions.md`

