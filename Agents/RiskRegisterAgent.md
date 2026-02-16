# 🟣 Agent Instructions

## Purpose
This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Output/Findings/` using the relevant template.
3. Capture confirmed facts under `Output/Knowledge/` (Confirmed + Assumptions). Keep it focused on reusable environment facts used during triage (services in use, identity model, network posture, guardrails).
   - Keep provider knowledge files consistent: use `## Confirmed`, `## Assumptions`, and
     `## Unknowns` headings (avoid ad-hoc extra sections).
   - If you need an append-only audit trail (e.g., bulk imports, Q&A/triage decisions), write it under `Output/Audit/` and clearly mark it as **AUDIT LOG ONLY — do not load into LLM triage context**.
4. Update `Output/Summary/` outputs (cloud resource summaries and risk register).

## Behaviour
- **Kickoff trigger:** if the user types `sessionkickoff` (case-insensitive), treat it as “run the session kickoff”.
  - Read `AGENTS.md` and `Agents/Instructions.md`, then scan `Output/Knowledge/` and existing `Output/Findings/` for missing context.
  - If there are **no findings** under `Output/Findings/`, assume this is a **new instance** and move straight to collecting the first triage input (single issue, bulk `Intake/` path, sample import, or repo scan).
  - **Preferred workspace scan (stdout-only):**
    - `python3 Scripts/scan_workspace.py`
    It scans `Output/Knowledge/` (refinement questions), `Output/Findings/`, and common `Intake/`/sample paths.
  - **Targeted helpers (stdout-only):**
    - **Check `Output/Knowledge/`:** `python3 Scripts/scan_knowledge_refinement.py`
      It lists Markdown files under `Output/Knowledge/` and prints any non-empty sections under `## Unknowns` / `## ❓ Open Questions`.
    - **Enumerate `Intake/` files:** `python3 Scripts/scan_intake_files.py <Intake/Subfolder>`
      It walks the filesystem and lists `.txt` / `.csv` / `.md` reliably (avoid relying on recursive globbing, which can be flaky on some WSL/Windows mounts).
    - **Check whether `Output/Findings/` has anything in it:** `python3 Scripts/scan_findings_files.py`
      It walks `Output/Findings/` and lists `.md` files reliably.
  - If `Output/Knowledge/` contains outstanding items under `## Unknowns` and/or `## ❓ Open Questions`, tell the user: “I’ve found some **refinement questions** — do you want to answer them now?” (then offer *resume* vs *proceed to new triage*).
  - If there are **no refinement questions** *and* the `Output/Knowledge/` scan indicates **no Knowledge markdown files** (i.e., `scan_knowledge_refinement.py` reports `Knowledge markdown files: 0`), treat this as a **first run / fresh workspace** and start with:
    - `🦖 Welcome to Triage-Saurus.`
  - Then ask the user to either **copy/paste a single issue** to triage, **provide a path under `Intake/`** to process in bulk, **import and triage the sample findings** (from `Sample Findings/` into `Intake/Sample/`), or **scan a repo**.
    - If they choose bulk intake, present a **selectable** multiple-choice list of common paths (and allow freeform for a custom `Intake/...` path).
      - Do **not** include numeric prefixes in the choice labels; the UI will handle numbering/selection.
      - Before offering choices, verify which candidate folders are **non-empty** using (stdout-only):
        - `python3 Scripts/scan_intake_files.py <candidate-path>`
      - Only offer **non-empty** candidates as choices.
    - **Idempotency (multi-day runs):** before processing a selected intake path, check for overlap with already-processed findings and only proceed with *new* items.
      - Run (stdout-only): `python3 Scripts/compare_intake_to_findings.py --intake <Intake/...> --findings Output/Findings/Cloud`
      - If **duplicates are detected** (Already processed > 0), **ask for confirmation** before proceeding:
        - proceed with **new items only** (recommended), or
        - stop and let the user adjust the intake.
      - If **no new items** remain, stop and tell the user.
      - If **new items exist**, proceed using only that new-item subset.
      - Candidate paths in this repo: `Intake/Cloud`, `Intake/Code`, `Intake/Sample/Cloud` (if present), `Intake/Sample/Code` (if present)
- After summarising what you’ve done (kickoff, scans, imports, bulk triage, file writes), always ask the user what they want to do next.
- If triage type is **Cloud** and the provider is not explicit from the folder path, quickly skim the intake titles and:
  - if they strongly indicate a provider, state it plainly (e.g., “From looking at the items to triage, it looks like you are using Azure.”)
  - then ask a single confirmation question prefixed with `❓` **on its own line**.
    - Use the provider name as the choice label (avoid “Yes (Azure)”): `Azure` / `AWS` / `GCP` / `Don’t know` (freeform allowed for other).
    - In the line *above* the `❓` question, include a brief “why” based on the titles (e.g., 🤔 Key Vault / Entra / Defender ⇒ Azure).
- Follow `Settings/Styling.md` for formatting rules.
  - In `Output/Summary/`, ensure any references to findings are **markdown links** (clickable),
    not inline-code backticks.
- At session start, quickly review existing `Output/Knowledge/` and any existing findings under `Output/Findings/` to spot missing context; ask targeted questions to fill gaps before proceeding.
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
- When you adjust a finding score based on user confirmation or `Output/Knowledge/`, add a one-line note (e.g., `Score change: 5/10 ➜ 7/10 — confirmed internet-facing prod exposure`).
- **Applicability check (per finding):**
  - For *evidence-backed* findings (e.g., scanner output that clearly indicates a failing resource), treat applicability as **Yes** by default and ask only scoping questions that change severity/remediation.
  - For *recommendation-style* findings where applicability is genuinely unclear, ask one question to establish whether the condition is currently true (Yes / No / Don’t know).
  - **Conflicting signals / partial coverage rule:** if user-provided context says a control is enabled (e.g., “Yes, WAF is enabled”) but the finding still exists (or other evidence suggests it’s not universal), assume **partial coverage**.
    - Do **not** downgrade based on the optimistic answer.
    - Default to the **worse (more severe) interpretation** and ask a single follow-up question explaining the conflict (e.g., multiple subscriptions/environments, or user uncertainty).
    - If the user answers “Yes — all”, but the finding indicates otherwise, keep severity higher until the scope is reconciled.
  - If applicability is **No** (confirmed false positive / out of scope), downgrade severity appropriately and rewrite the finding as a drift-prevention / assurance item.
- **Scope discipline:** do **not** create new findings that were not in the original
  input list (e.g., title-only export). It’s fine to:
  - add new environment context to `Output/Knowledge/`, and
  - update the *existing* finding to note: "if X is true, the score increases" (or
    set Applicability to **Yes** when confirmed).
- **Post-triage assumption confirmation:** after bulk triage (or whenever assumptions accumulate), ask follow-up questions to confirm/deny assumptions.
  - Ask **service-specific** questions where possible.
  - Ask **cross-cutting** questions once (e.g., “Are Private Endpoints used anywhere?”) and then apply the answer across relevant services.
  - Prefix these prompts with `❓` so they’re easy to spot in chat history.
- When asking or receiving answers to triage questions that influence scope,
  applicability, scoring, or remediation, append an entry to an `Output/Audit/` log
  (append-only) recording **the question + the user's answer** (including “Don’t
  know”). Only promote reusable facts into `Output/Knowledge/`.
- **Audit log size:** for bulk title imports, prefer an audit summary (count + source
  file path + timestamp). Only include per-item lists when the user explicitly asks.
- When kickoff questions are answered (triage type, cloud provider, repo path, scanner/source/scope, repo roots), check whether the answer adds new context vs existing `Output/Knowledge/`.
- **Repo scans:**
  - Prefer using `python3 Scripts/scan_repo_quick.py <abs-repo-path>` for an initial structure + module + secrets skim (stdout only).
  - Repo findings should include `## 🤔 Skeptic` with both `### 🛠️ Dev` and `### 🏗️ Platform` sections (same as Cloud/Code findings).
  - First check `Output/Knowledge/Repos.md` for known repo root path(s).
  - If it doesn’t exist or is empty, **suggest a default based on the current working directory**.
    - Prefer using the stdout-only helper to avoid guesswork: `python3 Scripts/get_cwd.py` (prints `cwd` + `suggested_repos_root`).
    - Then ask: **"I don’t currently know the root directory for your repos — should I use `<suggested path>`?"** (include **Yes / No / Don’t know**).
  - If the user confirms or provides an alternative, persist it into `Output/Knowledge/Repos.md`.
  - **Only after** at least one repo root is recorded (or the user explicitly confirms **"current repo"**), ask which repo/directory under that root should be scanned.
    - Accept either a single repo name/path, a list (comma/newline separated), or a simple wildcard/prefix pattern like `terraform-*`.
    - If the user provides a pattern/wildcard, **expand it into concrete repo names** and ask for an explicit confirmation of the expanded list before scanning.
    - If many repos match and the user hasn’t expressed a priority: scan shared module repos first (e.g., `*-modules`), then edge networking/security repos (network, firewall, gateway/WAF, DDoS), then identity, then data stores, then app/service repos.
  - Do not ask for language/ecosystem up-front; infer **languages + frameworks** from repo contents (lockfiles, build files, manifests, imports) and record them in the repo finding.
  - If new: append it **immediately** to `Output/Knowledge/` as **Confirmed** with a timestamp.
  - If already captured: don’t duplicate.
  - If Cloud + provider is confirmed: immediately update `Output/Summary/Cloud/Architecture_<Provider>.md`.
- Prefer confirmed facts, **but capture inferred context** in `Output/Knowledge/` as an
  explicit **assumption** and then ask the user to confirm/deny.
- When a finding implies additional environment context (e.g., “Defender for Cloud” recommendations imply Defender is enabled), record it in `Output/Knowledge/` as an **assumption** and immediately ask the user to confirm/deny.
- When findings reference a specific cloud service as the **subject** of the finding (e.g., AKS, Key Vault, Storage Accounts), record that service as **Confirmed in use** in `Output/Knowledge/` without asking (the finding itself implies the service exists).
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
  - Still update `Output/Knowledge/` with inferred services/controls as **assumptions**, then ask the
    user to verify the assumptions as a follow-up step.
- Keep findings actionable: impact, exploitability, and concrete remediation.
  - The `### Summary` section should start with a **business-impact** sentence. The Risk
    Register “Business Impact” column is a **single short sentence** for management and
    should avoid countermeasure/implementation detail.
- When a finding is created or updated, **immediately** update `Output/Knowledge/` with any
  new inferred or confirmed facts discovered while writing the finding.
  - Capture inferred facts as **assumptions** and ask the user to confirm/deny.
  - Prefer reusable environment knowledge (services in use, guardrails, identity
    model, network defaults, dependencies/modules) over one-off resource IDs.
  - It is OK to list dependencies/modules (including private/internal module repos).
  - **Repo finding Key Evidence section:**
    - Use emoji markers: 💡 (in use/neutral signal), ✅ (security-positive), ❌ (security-negative)
    - For secret-like signals: check module context before flagging
      - If inside a secure module (e.g., Key Vault storage), use 💡 or ✅
      - Only flag as ❌ if cleartext exposure or insecure handling is confirmed
    - For language/framework detection: infer from lockfiles/build files (*.tf = Terraform, go.mod = Go, package.json = Node.js, etc)
      - Do NOT report CI systems or containers as languages
      - The scan script now outputs a "Languages/frameworks detected" section - use that
  - **Repo finding Overview "Evidence for detection":**
    - If single evidence file: show inline
    - If multiple evidence files: format as bullet list
  - If a repo scan finds **Terraform module usage**, automatically:
    1) extract and classify module dependencies using (stdout-only): `python3 Scripts/analyze_terraform_modules.py <repo-path>`
    2) scan any **local-path** modules immediately,
    3) for any module repo/path that is **not already recorded in `Output/Knowledge/Repos.md` (or otherwise known)**, ask the user whether you can scan it next to increase context/accuracy,
       - if the module source is a remote git URL (e.g., Azure DevOps/GitHub), first ask the user for the **local path** (or confirmation it exists under a known repo root) before attempting any scan,
       - for **Terraform Registry modules** (registry.terraform.io), **do not ask to scan them**; just record them as upstream dependencies in the repo finding/audit.
       - use `python3 Scripts/scan_repo_quick.py` for the initial scan.
    4) repeat this process recursively for newly scanned module repos until no new modules are discovered (or the user says stop).
  - **Terraform module value resolution:** when reviewing Terraform code that calls modules, do not assume a variable/output implies insecure behaviour in the root module.
    - Example: a variable named `secret` or an output named `client_secret` may be passed into a module that stores it in Key Vault and only returns a reference/ID.
    - Rule: if a repo uses modules, treat security-relevant intent (secrets handling, network exposure defaults, RBAC) as **potentially hidden inside modules**; prioritise scanning the module code before drawing conclusions.
    - If the module source is not locally available, ask the user for the local path (per the module discovery rules) so you can confirm how values are actually handled.
    - If you have **reasonable suspicion** (e.g., an output/variable appears secret-like, a pipeline consumes a value as a secret, or a resource suggests public exposure) and you cannot confirm intent from available code, it is OK to:
      - ask a single `❓` confirmation question, and/or
      - add a short **"Follow-up task for repo owners"** bullet in the repo finding describing what to verify (and where).
  - If a dependency/module points to another company repo (e.g., Terraform modules), ask the user to provide that repo next for better context.
  - For Dockerfiles, capture both the **dev/local image** and the **shipping/runtime base image** (often multi-stage builds with multiple `FROM` lines; the later stages are commonly the shipped service base).
  - When you discover CI/CD (pipelines, runners, deploy scripts), it is OK to ask clarification questions about:
    - where secrets are stored (vault vs CI variables vs cloud secret store) and whether they are encrypted/rotated,
    - how CI/CD authenticates to the target environment (OIDC/workload identity vs long-lived keys/service principals),
    - and how CI/CD reaches the environment (network path, VPN/peering, private endpoints).
  - If you detect **Hiera** (YAML hierarchy/overrides), treat it as an environment-scope signal, but **do not** ask about environment tiers during the repo scan itself. Record it in `Output/Knowledge/` as an **Assumption** and defer any environment-scope questions until the user starts cloud triage (or explicitly requests environment scoping).
- When `Output/Knowledge/` is created or updated (including assumptions), **immediately**
  generate or update the provider architecture diagram under `Output/Summary/Cloud/` (e.g.,
  `Output/Summary/Cloud/Architecture_Azure.md`) to reflect the current known state and
  include any newly discovered services.
  - This is a **standing rule throughout the session** (do not wait until session
    kickoff or the end of triage).
  - Draw the diagram **from the internet inwards** (request flow / access paths).
  - Prefer **top-down** Mermaid (`flowchart TB`) so external → internal flows read naturally.
  - Only include **confirmed services** on the Mermaid diagram unless the user explicitly asks
    to include assumed components.
- While writing/updating cloud findings, scan the finding content for implied **cloud services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Output/Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
- **Finding content completeness:** ensure all findings have:
  - A clear **Overall Score** with severity and numeric score (e.g., `🔴 High 8/10`)
  - Proper **Summary** section (not generic boilerplate)
  - **Key Evidence** section with specific resource IDs, paths, or context
  - **Applicability** section with clear status (Yes/No/Don't know) and evidence
  - These sections are used by the risk register generator for accurate resource type classification and issue extraction
- When a recommendation depends on **platform SKU/tier/feature availability** (common examples: private endpoints, private registries, WAF features, auditing tiers), explicitly call out the dependency and note that remediation may require a **SKU change** (e.g., ACR private connectivity may require Premium depending on the provider/service).
- When a recommendation may require **reprovisioning/redeployment/restart** to take effect, explicitly warn about potential **downtime/maintenance windows** and rollout sequencing.
- For findings that materially affect platform operations (SKU changes, networking primitives, CI/CD constraints, or downtime risk), add a platform-engineering perspective under `## 🤔 Skeptic` → `### 🏗️ Platform` (see `Agents/PlatformSkeptic.md`).
- When a new finding overlaps an existing one, link them under **Compounding Findings**.
- **Avoid running git commands by default** (e.g., `git status`, `git diff`, `git restore`). Only use git when the user explicitly asks, and explain why it’s needed.
- **Avoid running scripts/automations by default**. If you propose running a script (including repo utilities like `python3 Scripts/risk_register.py`), first explain:
  - what it does,
  - what files it will write/change,
  - why it’s necessary now.
  - **Exception:** during **repo scans**, it is OK (and preferred) to run `python3 Scripts/scan_repo_quick.py <abs-repo-path>` as the default initial skim.
- **Automation language preference:** when automating a repo task, prefer **Python** over other
  languages to minimize extra dependencies the user may need to install.

## Outputs

- **Default behaviour:** outputs under `Output/Findings/`, `Output/Knowledge/`, and `Output/Summary/` are
  **generated per-user/session and are intentionally untracked** (see `.gitignore`).
  Change that only if you explicitly want to commit triage artifacts.

- **Cloud findings:** `Output/Findings/Cloud/<Titlecase>.md`
- **Code findings:** `Output/Findings/Code/<Titlecase>.md`
- **Repo scans:** `Output/Findings/Repo/Repo_<RepoName>.md` (one file per repo)
- **Cloud summaries:** `Output/Summary/Cloud/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Scripts/risk_register.py`
- **Optional bulk draft generator (titles → findings):** `python3 Scripts/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
  - With `--update-knowledge`, it also generates `Output/Summary/Cloud/*.md` per-service summaries, regenerates
    `Output/Summary/Risk Register.xlsx`, and appends audit entries under `Output/Audit/`.

## After changes to findings
- **Risk register must stay current:** after creating or updating any finding, regenerate:
  - `python3 Scripts/risk_register.py` (updates `Output/Summary/Risk Register.xlsx`)
- If you need a quick, consistent score list (for summaries/architecture notes), run:
  - `python3 Scripts/extract_finding_scores.py Output/Findings/Cloud`
  - Output: a Markdown table to stdout (Finding link + **Overall Score** + description).

## Utility scripts
- **Clear session artifacts (destructive):**
  - Dry-run: `python3 Scripts/clear_session.py`
  - Delete: `python3 Scripts/clear_session.py --yes`

- Ensure each finding includes:
  - `## 🗺️ Architecture Diagram` **directly under the title** (first section, before Overview)
  - `- **Overall Score:** <severity> <n>/10` **immediately after the diagram** (before Overview)
  - `## Meta Data` as the final section in the file
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
  - **All finding types** (Cloud, Code, Repo) must include the Architecture Diagram section

## Draft Finding Detection

- The spreadsheet includes a **Status** column that flags draft findings
- **"✅ Validated"**: Finding has been triaged with evidence, applicability confirmation, and accurate scoring
- **"⚠️ Draft - Needs Triage"**: Finding has generic boilerplate from title-only input and needs:
  - Applicability confirmation (Yes/No/Don't know)
  - Specific evidence (resource IDs, query output, screenshots)
  - Environment context (production, internet-facing, etc.)
  - Accurate risk scoring based on actual exposure
- Draft findings typically have placeholder 5/10 Medium scores
- Use `python3 Scripts/check_draft_findings.py` to identify draft findings needing validation
- During session kickoff, prompt users to complete draft findings if >10% are unvalidated
