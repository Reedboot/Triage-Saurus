# 🟣 Agent Instructions

## Purpose
This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Findings/` using the relevant template.
3. Capture confirmed facts under `Knowledge/` (Confirmed + Assumptions). Keep it focused on reusable environment facts used during triage (services in use, identity model, network posture, guardrails).
   - Keep provider knowledge files consistent: use `## Confirmed`, `## Assumptions`, and
     `## Unknowns` headings (avoid ad-hoc extra sections).
   - If you need an append-only audit trail (e.g., bulk imports, Q&A/triage decisions), write it under `Audit/` and clearly mark it as **AUDIT LOG ONLY — do not load into LLM triage context**.
4. Update `Summary/` outputs (cloud resource summaries and risk register).

## Behaviour
- **Kickoff trigger:** if the user types `sessionkickoff` (case-insensitive), treat it as “run the session kickoff”.
  - Read `AGENTS.md` and `Agents/Instructions.md`, then scan `Knowledge/` and existing `Findings/` for missing context.
  - **How to check `Knowledge/`:** list markdown files under `Knowledge/` (including top-level files like `Knowledge/Azure.md`, not only subfolders). Avoid relying on recursive glob patterns (they’re not consistently supported across all environments); prefer a filesystem listing (e.g., `find Knowledge -type f -name '*.md'`) and then search those files for headings `## Unknowns` and `## ❓ Open Questions` and treat any non-empty section as outstanding.
  - If `Knowledge/` contains outstanding items under `## Unknowns` and/or `## ❓ Open Questions`, tell the user: “I’ve found some **refinement questions** — do you want to answer them now?” (then offer *resume* vs *proceed to new triage*).
  - Then ask the user to either **copy/paste a single issue** to triage, **provide a path under `Intake/`** to process in bulk, **import and triage the sample findings** (from `Sample Findings/` into `Intake/Sample/`), or **scan a repo**.
- Follow `Settings/Styling.md` for formatting rules.
  - In `Summary/`, ensure any references to findings are **markdown links** (clickable),
    not inline-code backticks.
- At session start, quickly review existing `Knowledge/` and any existing findings under `Findings/` to spot missing context; ask targeted questions to fill gaps before proceeding.
- **UK spelling in user-facing questions:** use UK English (e.g., *prioritise, assess, organisation*).
- Ask one targeted question at a time; avoid bundling multiple confirmations into a single prompt.
- For **info-gathering / refinement** questions, avoid broad prompts (e.g., “Do you have internet-facing workloads?”). Instead:
  - briefly state the purpose (e.g., “To better assess applicability and prioritisation…”), and
  - ask a bounded, easy-to-answer question (prefer multiple-choice categories such as *public customer-facing*, *mostly private/internal*, *private-only*), and
  - include **“Don’t know”**.
- When asking **multiple-choice** questions, always include a **“Don’t know”** option.
- When asking a triage question:
  - start with `📌 Trigger:` `<finding title>`
  - then ask exactly one question prefixed with `❓`
  - include the service name so the question is understandable out of context.
  - include a lightweight progress indicator with remaining count (e.g., `Progress: Q3/10 (7 left)`) when doing bulk refinement.
  - every 5 questions, remind the user they can pause and resume refinement at any time, and include the estimated remaining *batches of 5* (e.g., `~3 batches left`).
  - avoid asking tautological applicability questions when the input already implies at least one affected resource (e.g., title-only exports phrased as “should …`).
- When you adjust a finding score based on user confirmation or Knowledge/, add a one-line note (e.g., `Score change: 5/10 ➜ 7/10 — confirmed internet-facing prod exposure`).
- **Applicability check (per finding):**
  - For *evidence-backed* findings (e.g., scanner output that clearly indicates a failing resource), treat applicability as **Yes** by default and ask only scoping questions that change severity/remediation.
  - For *recommendation-style* findings where applicability is genuinely unclear, ask one question to establish whether the condition is currently true (Yes / No / Don’t know).
  - If applicability is **No**, downgrade severity appropriately and rewrite the finding as a drift-prevention / assurance item.
- **Scope discipline:** do **not** create new findings that were not in the original
  input list (e.g., title-only export). It’s fine to:
  - add new environment context to `Knowledge/`, and
  - update the *existing* finding to note: "if X is true, the score increases" (or
    set Applicability to **Yes** when confirmed).
- **Post-triage assumption confirmation:** after bulk triage (or whenever assumptions accumulate), ask follow-up questions to confirm/deny assumptions.
  - Ask **service-specific** questions where possible.
  - Ask **cross-cutting** questions once (e.g., “Are Private Endpoints used anywhere?”) and then apply the answer across relevant services.
  - Prefix these prompts with `❓` so they’re easy to spot in chat history.
- When asking or receiving answers to triage questions that influence scope,
  applicability, scoring, or remediation, append an entry to an `Audit/` log
  (append-only) recording **the question + the user's answer** (including “Don’t
  know”). Only promote reusable facts into `Knowledge/`.
- **Audit log size:** for bulk title imports, prefer an audit summary (count + source
  file path + timestamp). Only include per-item lists when the user explicitly asks.
- When kickoff questions are answered (triage type, cloud provider, repo path, scanner/source/scope, repo roots), check whether the answer adds new context vs existing `Knowledge/`.
- **Repo scans:** first check `Knowledge/Repos.md` for known repo root path(s).
  - If it doesn’t exist or is empty, **suggest a default based on the current working directory** (e.g., parent folder of the current repo) and ask: **"I don’t currently know the root directory for your repos — should I use `<suggested path>`?"** (include **Yes / No / Don’t know**).
  - If the user confirms or provides an alternative, persist it into `Knowledge/Repos.md`.
  - **Only after** at least one repo root is recorded (or the user explicitly confirms **"current repo"**), ask which repo/directory under that root should be scanned.
  - Do not ask for language/ecosystem up-front; infer **languages + frameworks** from repo contents (lockfiles, build files, manifests, imports) and record them in the repo finding.
  - If new: append it **immediately** to `Knowledge/` as **Confirmed** with a timestamp.
  - If already captured: don’t duplicate.
  - If Cloud + provider is confirmed: immediately update `Summary/Cloud/Architecture_<Provider>.md`.
- Prefer confirmed facts, **but capture inferred context** in `Knowledge/` as an
  explicit **assumption** and then ask the user to confirm/deny.
- When a finding implies additional environment context (e.g., “Defender for Cloud” recommendations imply Defender is enabled), record it in `Knowledge/` as an **assumption** and immediately ask the user to confirm/deny.
- When findings reference a specific cloud service as the **subject** of the finding (e.g., AKS, Key Vault, Storage Accounts), record that service as **Confirmed in use** in `Knowledge/` without asking (the finding itself implies the service exists).
  - This also applies to **bulk title-only imports**: if a title clearly names an Azure service (e.g., “secure transfer on storage accounts”, “enable SQL auditing”, “disable ACR admin user”), treat that service as **Confirmed in use**.
- If a finding recommends enabling an **additional** service/control (e.g., DDoS Standard, Defender plan, Private Link), record that additional service/control as an **Assumption** until the user confirms.
- When processing findings in bulk (including sample findings), process items **sequentially**.
  - Use a default priority order unless the user overrides it:
    1) Internet exposure (public SSH/RDP, public PaaS endpoints, public management planes)
    2) High-value data stores (SQL/Cosmos/Storage) and secrets (Key Vault)
    3) Identity/privilege guardrails (owners/RBAC)
    4) Detection/logging/monitoring
    5) Hardening baselines
  - After completing one finding, **immediately continue to the next finding** without asking
    “should I continue?”.
  - Only pause for user input when you need a decision that materially changes remediation,
    applicability, scoring, or scope.
  - Still update `Knowledge/` with inferred services/controls as **assumptions**, then ask the
    user to verify the assumptions as a follow-up step.
- Keep findings actionable: impact, exploitability, and concrete remediation.
  - The `### Summary` section should start with a **business-impact** sentence. The Risk
    Register “Business Impact” column is a **single short sentence** for management and
    should avoid countermeasure/implementation detail.
- When a finding is created or updated, **immediately** update `Knowledge/` with any
  new inferred or confirmed facts discovered while writing the finding.
  - Capture inferred facts as **assumptions** and ask the user to confirm/deny.
  - Prefer reusable environment knowledge (services in use, guardrails, identity
    model, network defaults, dependencies/modules) over one-off resource IDs.
  - It is OK to list dependencies/modules (including private/internal module repos). If a dependency/module points to another company repo (e.g., Terraform modules), ask the user to provide that repo next for better context.
  - For Dockerfiles, capture both the **dev/local image** and the **shipping/runtime base image** (often multi-stage builds with multiple `FROM` lines; the later stages are commonly the shipped service base).
  - When you discover CI/CD (pipelines, runners, deploy scripts), it is OK to ask clarification questions about:
    - where secrets are stored (vault vs CI variables vs cloud secret store) and whether they are encrypted/rotated,
    - how CI/CD authenticates to the target environment (OIDC/workload identity vs long-lived keys/service principals),
    - and how CI/CD reaches the environment (network path, VPN/peering, private endpoints).
  - If you detect **Hiera** (YAML hierarchy/overrides), treat it as an environment-scope signal, but **do not** ask about environment tiers during the repo scan itself. Record it in `Knowledge/` as an **Assumption** and defer any environment-scope questions until the user starts cloud triage (or explicitly requests environment scoping).
- When `Knowledge/` is created or updated (including assumptions), **immediately**
  generate or update the provider architecture diagram under `Summary/Cloud/` (e.g.,
  `Summary/Cloud/Architecture_Azure.md`) to reflect the current known state and
  include any newly discovered services.
  - This is a **standing rule throughout the session** (do not wait until session
    kickoff or the end of triage).
  - Draw the diagram **from the internet inwards** (request flow / access paths).
  - Prefer **top-down** Mermaid (`flowchart TB`) so external → internal flows read naturally.
  - Only include **confirmed services** on the Mermaid diagram unless the user explicitly asks
    to include assumed components.
- While writing/updating cloud findings, scan the finding content for implied **cloud services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
- When a recommendation depends on **platform SKU/tier/feature availability** (common examples: private endpoints, private registries, WAF features, auditing tiers), explicitly call out the dependency and note that remediation may require a **SKU change** (e.g., ACR private connectivity may require Premium depending on the provider/service).
- When a recommendation may require **reprovisioning/redeployment/restart** to take effect, explicitly warn about potential **downtime/maintenance windows** and rollout sequencing.
- For findings that materially affect platform operations (SKU changes, networking primitives, CI/CD constraints, or downtime risk), add a platform-engineering perspective under `## 🤔 Skeptic` → `### 🏗️ Platform` (see `Agents/PlatformSkeptic.md`).
- When a new finding overlaps an existing one, link them under **Compounding Findings**.
- **Avoid running git commands by default** (e.g., `git status`, `git diff`, `git restore`). Only use git when the user explicitly asks, and explain why it’s needed.
- **Avoid running scripts/automations by default**. If you propose running a script (including repo utilities like `python3 Skills/risk_register.py`), first explain:
  - what it does,
  - what files it will write/change,
  - why it’s necessary now.
- **Automation language preference:** when automating a repo task, prefer **Python** over other
  languages to minimize extra dependencies the user may need to install.

## Outputs

- **Default behaviour:** outputs under `Findings/`, `Knowledge/`, and `Summary/` are
  **generated per-user/session and are intentionally untracked** (see `.gitignore`).
  Change that only if you explicitly want to commit triage artifacts.

- **Cloud findings:** `Findings/Cloud/<Titlecase>.md`
- **Code findings:** `Findings/Code/<Titlecase>.md`
- **Repo scans:** `Findings/Repo/Repo_<RepoName>.md` (one file per repo)
- **Cloud summaries:** `Summary/Cloud/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Skills/risk_register.py`
- **Optional bulk draft generator (titles → findings):** `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
  - With `--update-knowledge`, it also generates `Summary/Cloud/*.md` per-service summaries, regenerates
    `Summary/Risk Register.xlsx`, and appends audit entries under `Audit/`.

## After changes to findings
- **Risk register must stay current:** after creating or updating any finding, regenerate:
  - `python3 Skills/risk_register.py` (updates `Summary/Risk Register.xlsx`)
- If you need a quick, consistent score list (for summaries/architecture notes), run:
  - `python3 Skills/extract_finding_scores.py Findings/Cloud`
  - Output: a Markdown table to stdout (Finding link + **Overall Score** + description).

## Utility scripts
- **Clear session artifacts (destructive):**
  - Dry-run: `python3 Skills/clear_session.py`
  - Delete: `python3 Skills/clear_session.py --yes`

- Ensure each finding includes:
  - `- **Overall Score:** <severity> <n>/10`
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
