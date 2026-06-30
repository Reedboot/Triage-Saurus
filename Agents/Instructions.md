# 🟣 Agent Instructions

## Purpose

> Agents should persist any script/diagram/template changes made during experiments back into the repository under Scripts/, Templates/, and Rules/ so they are available for future runs; avoid committing without user confirmation but write files in-place as part of the experiment workflow.


This repository supports consistent security triage. The expected workflow is:
1. Triage an issue (cloud or code).
2. Create/update a finding under `Output/Findings/` using the relevant template.
3. Capture confirmed facts under `Output/Knowledge/` (Confirmed + Assumptions). Keep it focused on reusable environment facts used during triage (services in use, identity model, network posture, guardrails).
   - Keep provider knowledge files consistent: use `## Confirmed`, `## Assumptions`, and
     `## Unknowns` headings (avoid ad-hoc extra sections).
   - If you need an append-only audit trail (e.g., bulk imports, Q&A/triage decisions), write it under `Output/Audit/` and clearly mark it as **AUDIT LOG ONLY — do not load into LLM triage context**.
4. Update `Output/Summary/` outputs (cloud resource summaries and risk register).
5. **Write security rules** for new detections or missing checks in `Rules/` folder.

**Navigation:** For menu structures and user journey flows, see `Templates/Workflows.md`.

## 📋 Detection Rules Architecture

**Rules as Single Source of Truth:**
- All security checks MUST be defined as rules in `Rules/` folder
- Rules are declarative (WHAT to check)
- Scripts are imperative (HOW to execute checks)
- Use opengrep/Semgrep-compatible format

### Mandatory opengrep Execution

- When opengrep is installed (default repo state), every IaC/code scan MUST start with `opengrep scan --config Rules/ <target>` to apply the entire ruleset.
- Treat opengrep as the primary enforcement mechanism; manual grep fallbacks are not permitted. If a resource type is missing detection, add a Detection rule under Rules/Detection and map it in DETECTION_TO_MISCONFIG so scans include the appropriate misconfig checks.
- Record the opengrep command, target path, and timestamp in the audit log under **Actions Log**.

### Rule Creation & Verification

- **When creating new rules** (regardless of experiment or not), create them in the relevant subfolder of `Rules/` (e.g., `Rules/Detection/`, `Rules/Misconfigurations/`, `Rules/Misconfigurations/Secrets/`).
- **Rules must pass the Rule Genericity Gate** (see `Rules/CreationGuide.md`): rules must work against any codebase of the same technology, not just the project being scanned. There are three failure modes:
  - **Too specific** (❌): pattern contains a project-specific identifier — resource name, variable name, tenant/subscription ID, hostname, org name. Replace with metavariables (`$VAR`) or constrained regex.
  - **Too broad** (❌): pattern fires on almost any codebase regardless of security context (e.g., detects the word `linux`, or any variable named `password` with no structural constraint). Add structural context to narrow it.
  - **Just right** (✅): detects a known vulnerability class (CWE / OWASP / cloud provider benchmark) using portable patterns that fire on any vulnerable instance and not on correctly-configured code.
  - If a finding cannot be expressed as a portable rule, document it directly in the findings file — do not force it into the shared ruleset.
- **After creating a rule**, run the mandatory validation script before committing — this covers both opengrep syntax validation and portability checks in one step:
  ```bash
  python3 Scripts/Validate/validate_rule_portability.py <rule-file.yml>
  ```
  The script must exit `0`. A non-zero exit means the rule has syntax errors or hardcoded project-specific identifiers and **must not be committed**.
- **Current limitation (Mar 2026):** opengrep 1.16.1/1.16.2 can hang on WSL when a single scan processes more than ~900 git-tracked files (≈8 large subdirectories). Use `python3 Scripts/Scan/opengrep_chunked_scan.py <target>` to automatically batch scans into safe-size chunks, logging each chunk until the upstream fix lands.
- **Context window hygiene:** After each repo scan, summarize key learnings (resources, dependencies, unanswered questions) into `Output/Knowledge/<...>.md` and the Cozo knowledge graph, then purge the working context (clear scratch buffers, stop streaming agents) before starting the next repo so the LLM never carries stale assumptions between scans. Always reload only the relevant knowledge slices for the next repo from the Cozo graph rather than keeping prior repo transcripts in memory.

**Rules + LLM Pattern:**
- Rules detect patterns and extract relevant data
- Rules MUST be scoped to specific services/resources (e.g., `azure-storage-logging-disabled`) for deterministic coverage
- LLM reviews the concrete rule hits for context-specific assessment (severity, compensating controls, remediation urgency)
- LLMs MUST reason about compromise chains, not just isolated findings: if a resource compromise yields an identity token, broad RBAC role, automation control, or inherited managed identity, explicitly trace the reachable attack path and blast radius.
- LLMs MUST distinguish direct internet reachability from authenticated public endpoints: add Internet arrows for public data services and annotate whether access is anonymous/public or public-endpoint-with-authentication.
- Example: Rule detects Ubuntu version → LLM checks if EOL or subject to CVE
- Benefits: Rules stay precise, LLM provides fresh context without deciding what to flag

### When to Create a Rule

✅ **Create a new rule when:**
1. You discover a new security issue not covered by existing rules
2. A scan misses a vulnerability that should be detected
3. You identify a pattern that should be systematically checked
4. Learning from external sources (tool output, security research, incidents)
5. Extracting checks from scripts into declarative format

### Rule Creation Guidelines

**Location:** `Rules/Misconfigurations/` or `Rules/Misconfigurations/Secrets/`

**Format:** Opengrep/Semgrep YAML
```yaml
rules:
  - id: unique-rule-identifier
    message: |
      Brief description of the issue and remediation.
    severity: ERROR | WARNING | INFO
    languages: [terraform, yaml, etc.]
    pattern: |
      code pattern to match
    metadata:
      category: security
      subcategory: [specific area]
      cwe: CWE-XXX
      technology: [terraform, azure, kubernetes]
      five_pillars: Pillar X (if applicable)
```

**Required Fields:**
- `id`: Lowercase with hyphens (e.g., `azure-sql-auditing-disabled`)
- `message`: Clear description + remediation advice
- `severity`: ERROR (critical), WARNING (high/medium), INFO (low)
- `metadata.cwe`: CWE reference if applicable
- `metadata.technology`: What this rule applies to

### Tracking Rule Detection in Findings

All findings MUST reference the rule that detected them:
- Add `detected_by_rule: rule-id` in finding metadata
- If manual detection, use `detected_by_rule: manual`
- **Identity Best Practice:** Strongly recommend **IAM Roles for Service Accounts (IRSA)** or **Workload Identity Federation** over long-lived secrets. 
- **Container Security Best Practice:** Strongly recommend against `privileged: true`. Use granular Linux capabilities instead. 
- This enables tracking rule effectiveness

**CRITICAL: Apply ALL Rules, Not Selective Subsets**

**When scanning IaC/code:**
1. ✅ Run `opengrep scan --config Rules/ <target>` to apply ALL rules from `Rules/Misconfigurations/*.yml`. Rules are granular per service; do not rely on generic "detect resource then ask LLM" patterns. If opengrep fails, log the issue, fix the cause, and rerun the scan. Do not use manual grep fallbacks; instead add the missing detection rule under Rules/Detection.
2. ✅ Run skeptic reviews (DevSkeptic + PlatformSkeptic) for each finding
3. ✅ Adjust severity based on mitigating controls identified by skeptics
4. ✅ Document findings with proper rule metadata (`detected_by_rule: <rule-id>`)
5. ❌ NEVER apply selective rule subsets based on assumed scope
6. ❌ NEVER skip rules because "this type of issue seems unlikely"
7. ❌ NEVER defer detection to LLMs; LLMs only enrich rule hits.

---

## 🔍 Manual Security Review Best Practices

For manual code review patterns (trigger conditions, multi-tenant isolation checklist, custom auth scheme validation, header-based auth checks, and Phase 4 finding documentation requirements), see `Agents/SecurityAgent.md` — "Manual Review Checklists" section.

---

## 🚨 Environment Scoring Rules
See `Agents/SecurityAgent.md` — "Critical Rule: Scan All Environments with Production Rigor" for scoring rules, inherently-critical vulnerability list, and examples.

---

## 📚 Learning Capture

**CRITICAL:** When you discover bugs, patterns, or improvements during triage, capture learnings in the proper location:

### ✅ Correct Location: Output/Learning/
- **Experiment-specific learnings:** `Output/Learning/experiments/00X/LEARNING_*.md`
  - Example: `LEARNING_PROVIDER_DETECTION_BUG.md`
  - Example: `LEARNING_SKEPTIC_DISAGREEMENT_PATTERNS.md`
- **Cross-experiment insights:** `Output/Learning/insights/` (create if needed)
- **Strategy updates:** `Output/Learning/strategies/*.json`

### ❌ WRONG Locations (DO NOT USE):
- `~/.copilot/session-state/*/files/` - Session temp files, NOT persistent learning
- Agent subprocess contexts - Learnings won't be accessible to future sessions
- Session plan.md - Only for current task planning, gets archived

### When to Capture Learnings

**Immediate capture (during experiment):**
- Bugs in scripts/agents (like provider detection)
- Scoring disagreements between skeptics (patterns to learn from)
- False negatives/positives discovered during validation
- Detection techniques that worked particularly well

**Post-experiment capture (after user feedback):**
- Run LearningAgent to analyze `experiments/00X/validation.json`
- Compare experiments to identify patterns
- Update Cozo knowledge graph with effectiveness metrics

### Format for Learning Documents

```markdown
# LEARNING: [Brief Title]

## Context
- **Experiment:** 00X_RepoName_scan
- **Date:** YYYY-MM-DD
- **Trigger:** What caused this learning (user feedback, bug discovery, etc.)

## Issue Discovered
[Detailed description of problem]

## Root Cause
[Technical analysis]

## Recommended Fix
[Specific implementation steps]

## Impact
- Affects: [Which agents/scripts/future scans]
- Priority: P0/P1/P2
- Effort: [Estimated time]

## Verification
[How to test the fix works]
```

### LearningAgent Workflow

See `Agents/LearningAgent.md` for full process. Typical flow:
1. Human provides feedback on findings (validation.json)
2. Run: `python3 Scripts/analyze_experiment.py 006`
3. LearningAgent analyzes patterns, proposes changes
4. Changes applied to next experiment's Agents/ folder

---

## Behaviour
- **Kickoff trigger:** if the user types `sessionkickoff` (case-insensitive), treat it as “run the session kickoff”.
  - Read `AGENTS.md` and `Agents/Instructions.md`, then scan `Output/Knowledge/` and existing `Output/Findings/` for missing context.
  - If there are **no findings** under `Output/Findings/`, assume this is a **new instance** and move straight to collecting the first triage input (single issue, bulk `Intake/` path, sample import, or repo scan).
  - **Preferred workspace scan (stdout-only):**
- **Clear session trigger:** if the user types `clearsession` (case-insensitive), treat it as "run the clear session script".
  - First run dry-run: `python3 Scripts/clear_session.py` to show what will be deleted
  - Ask user: "This will delete the listed session artifacts (findings, knowledge, audit logs, summaries). Proceed?"
  - If confirmed, run: `python3 Scripts/clear_session.py --yes`
  - This clears all Output/ artifacts (Findings, Knowledge, Summary, Audit) and sample-staged Intake/Sample/ content while preserving Templates/ and user Intake/ files.
    - `python3 Scripts/Scan/scan_workspace.py`
    It scans `Output/Knowledge/` (refinement questions), `Output/Findings/`, and common `Intake/`/sample paths.
  - **Check for draft findings requiring validation:**
    - `python3 Scripts/Utils/check_draft_findings.py`
    It identifies findings with generic boilerplate that need evidence, applicability confirmation, and accurate risk scoring.
    - If **>10% of findings are drafts**, prominently warn the user and offer to complete them:
      - "⚠️ Found **N draft findings** that need validation. These have placeholder scores and generic boilerplate."
      - "Would you like to **validate draft findings** now, or **proceed with new triage**?"
    - Draft findings show as "⚠️ Draft - Needs Triage" in the Risk Register Status column.
    - **Draft completion workflow (question-first):** when the user chooses to validate drafts:
      - First, skim the draft set and identify **common missing context** before asking per-finding questions.
        - Preferred helper (stdout-only): `python3 Scripts/triage_queue.py`
      - Ask **cross-cutting** questions first (one at a time, prefix with `❓`) so answers apply to many findings, e.g.:
        - cloud provider confirmation (if missing)
        - production vs non-prod scoping (if known)
        - internet exposure posture (public endpoints, management ports)
        - private connectivity patterns (Private Endpoints/Private Link)
        - privileged access model (Bastion/JIT, jump hosts, allowlisted admin IPs)
        - storage access model (public blobs, shared keys)
      - Then apply those answers across the impacted drafts and only then go deeper into the highest-severity items.
      - **Do not** "validate" a finding by removing boilerplate text. Set validation explicitly using the finding `Validation Status` field (e.g., `⚠️ Draft - Needs Triage` vs `✅ Validated`) once evidence/applicability is sufficiently confirmed.
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
  - Then use the **ask_user tool** with selectable choices:
    - **Copy/paste a single issue to triage**
    - **Scan a specific repo**
    - **Run a batch scan using Intake/ReposToScan.txt (Batch)**


    - If they choose bulk intake, present a **selectable** multiple-choice list of common paths (and allow freeform for a custom `Intake/...` path).
      - Do **not** include numeric prefixes in the choice labels; the UI will handle numbering/selection.
      - Before offering choices, verify which candidate folders are **non-empty** using (stdout-only):
        - `python3 Scripts/scan_intake_files.py <candidate-path>`
      - Only offer **non-empty** candidates as choices.
  - **Multiple-choice questions (UX):**
    - When asking a multiple-choice question in plain chat, use **numbered bullet points** (e.g., `1. **Option text**`) for better readability.
      - Always include a **“Don’t know”** option as one of the numbered choices.
      - Accept either the **number** or the **full text** as a valid answer.
    - Exception: when using a **selectable** UI prompt (where the client renders choices), do **not** include numeric prefixes in the labels.
  - **Idempotency (multi-day runs):** before processing a selected intake path, check for overlap with already-processed findings and only proceed with *new* items.
    - Run (stdout-only): `python3 Scripts/Utils/compare_intake_to_findings.py --intake <Intake/...> --findings Output/Findings/Cloud`
    - If **duplicates are detected** (Already processed > 0), **ask for confirmation** before proceeding:
      - proceed with **new items only** (recommended), or
        - stop and let the user adjust the intake.
      - If **no new items** remain, stop and tell the user.
      - If **new items exist**, proceed using only that new-item subset.
      - Candidate batch source in this repo: `Intake/ReposToScan.txt`
- After summarising what you’ve done (kickoff, scans, imports, bulk triage, file writes), always ask the user what they want to do next.
- **Cloud context survey:** Before starting cloud triage (after provider is confirmed), check if `Output/Knowledge/<Provider>.md` has sufficient environmental context. If sparse/missing, offer: "Would you like to run a cloud context survey first? (~10 questions, builds foundational context for faster triage)" — see `Agents/CloudContextAgent.md` for the survey workflow.
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
  - If applicability is **No** (confirmed false positive / out of scope), mark as **FALSE POSITIVE** and handle based on source:
    - **External scanner source (Snyk, Aikido, Defender, etc.):** Recommendation should be to mark the finding as false positive IN THE TOOL ITSELF (not just document). This prevents repeated alerting and keeps the scanner signal-to-noise ratio high.
    - **Repo scan source (dotnet list package, npm audit, manual analysis):** No external tool to suppress in, so mark as **FALSE POSITIVE (Informative)** with `- **Overall Score:** 🟢 **FALSE POSITIVE (Informative)**`. Document the rationale for future reference but no remediation action required.
    - **Key distinction:** Scanner-based findings can be suppressed at source; repo scan findings cannot, so they're documentation only.
    - **Example false positive scenarios:** CVE applies to different runtime version, vulnerability requires feature not in use, transitive dependency not executed, environment-specific CVE that doesn't apply to this deployment.
- **Scope discipline:** do **not** create new findings that were not in the original
  input list (e.g., title-only export). It’s fine to:
  - add new environment context to `Output/Knowledge/`, and
  - update the *existing* finding to note: "if X is true, the score increases" (or
    set Applicability to **Yes** when confirmed).
- **Post-triage assumption confirmation:** after bulk triage (or whenever assumptions accumulate), ask follow-up questions to confirm/deny assumptions.
  - Ask **service-specific** questions where possible.
  - Ask **cross-cutting** questions once (e.g., “Are Private Endpoints used anywhere?”) and then apply the answer across relevant services.
  - Prefix these prompts with `❓` so they’re easy to spot in chat history.
- **Audit logging (MANDATORY for all sessions):**
  - Create `Output/Audit/Session_YYYY-MM-DD_HHMMSS.md` at the start of each triage session (use timestamp from session start).
  - **Log ALL of the following:**
    - **Session initialization:** triage type selected, cloud provider, intake path, repo path, scan scope
    - **Questions asked:** Every `❓` question asked during triage with timestamp
    - **User answers:** All user responses (including "Don't know" / freeform / multiple choice selections)
    - **Assumptions made:** When the agent infers context and records it as an assumption
    - **Actions taken:** Finding created/updated, Knowledge updated, Summary regenerated
    - **Bulk operations:** Import source, count of items, which items were processed
    - **Score changes:** When findings are rescored (initial → Dev skeptic → Platform skeptic)
  - **Audit log format:** Use the template from `Templates/AuditLog.md`. Mark each file `AUDIT LOG ONLY — do not load into LLM triage context`.
  - **Token tracking:** Removed from workflow. Focus audit logging on actions, questions, and findings; do not attempt to capture per-operation token/model data (proved inaccurate).
  - **When to append (not replace):** Always append to the session log, never overwrite previous entries.
  - **Audit log size:** for bulk title imports, prefer an audit summary (count + source file path + timestamp). Only include per-item lists when the user explicitly asks or when count is <20 items.
  - **Audit log is append-only:** These logs are for human review and compliance tracking, not for feeding back into context windows.

- When kickoff questions are answered (triage type, cloud provider, repo path, scanner/source/scope, repo roots), check whether the answer adds new context vs existing `Output/Knowledge/`.
- **Repo scans:** For the full workflow (context discovery phases, pre-scan sync, IaC/SCA/SAST procedures, module resolution, ingress tracing), see `Agents/RepoAgent.md`. Key constraints:
  - **🚨 VALIDATION RULE FOR EXPANSEAZURELAB (NO CHEATING):** Do NOT look at `attacks/` or `images/` folders during the scan phase — only review AFTER completing all findings to measure TP/FN/FP. Document in `validation.json`.
  - **Create repo summary FIRST:** Persist a TL;DR to the DB and create `Output/Summary/Repos/<RepoName>.md` before creating any findings. Use exact repo name as-is (no prefix/suffix).
  - **One finding per file** — NEVER create consolidated finding files. Each finding = a separate markdown file under `Output/Findings/<type>/<RepoName>/`.
  - **Mandatory skeptic reviews:** After creating findings, immediately run Dev + Platform Skeptic reviews in parallel (see `Agents/DevSkeptic.md` and `Agents/PlatformSkeptic.md`). Both agents update `### 🛠️ Dev` / `### 🏗️ Platform` sections before presenting the final summary.
  - **Scanner scope defaults to "IaC + SCA"** — SAST is available but not default.
  - **Code findings must be fully populated** (no FILL placeholders) — use evidence-backed content from the scan.
  - **Prioritise IaC/platform repos first** when available (names containing `*-modules`, `terraform-*`, `infrastructure`, `iac`).
  - **Multi-repo scans:** Ask permission before launching parallel scans. Start with batch of 3, adapt based on success/failure. After all scans complete, run a consolidation pass (cross-repo patterns, compounding issues, diagram sync, regenerate risk register).
  - **Cloud architecture extraction (MANDATORY):** When a repo scan discovers cloud services/IaC, immediately update `Output/Knowledge/<Provider>.md` and `Output/Summary/Cloud/Architecture_<Provider>.md`. `Architecture_<Provider>.md` is ALWAYS platform-wide — never scoped to a single repo. UPDATE, don't replace.
  - **Terraform module value resolution:** Treat security-relevant intent as potentially hidden inside modules. Scan local module code before drawing conclusions. For remote modules not available locally, ask the user for the path.
  - **IaC provider versions:** Record detected versions in `Output/Knowledge/Repos.md` under `## IaC Provider Versions`. Look up security-relevant provider defaults and record in `Output/Knowledge/<Provider>.md` under `## 🏗️ IaC Provider Defaults`.
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
  - **Validated summary refresh:** when a finding’s `Validation Status` is set to `✅ Validated`, replace any title-only boilerplate in `### 🧾 Summary` with a short, evidence-backed summary based on **confirmed** context (do not over-claim specific resource IDs if you don’t have them yet).
  - **TL;DR - Executive Summary:** After collaboration (Dev/Platform Skeptic reviews) is complete, **immediately populate** the `## 📊 TL;DR - Executive Summary` section (which should be placed immediately after the architecture diagram). This provides security engineers quick access to:
    - Final score with adjustment tracking (Security Review → Dev → Platform)
    - Top 3 priority actions with effort estimates
    - Material risks summary (2-3 sentences)
    - Why the score changed (if adjustments were made)
    - **Critical:** The TL;DR must be populated by the skeptic review agents, not left as a placeholder. If using task agents for skeptic reviews, instruct them to populate the TL;DR section.
  - **Overall Score reconciliation:** After Dev and Platform Skeptic reviews are complete, update the top-level `- **Overall Score:**` line to show the full score progression. Format: `<emoji> **X/10** (<severity>) — *Final: Security Y/10 → Dev [✅/⬇️/⬆️]Z/10 → Platform [✅/⬇️/⬆️]X/10*` where X is the final reconciled score. This shows transparency in the decision-making process and which skeptic's recommendation was accepted.
  - **Validation Required:** If there are critical **unconfirmed assumptions** that could significantly change the risk score, add a `## ❓ Validation Required` section immediately after the TL;DR. This must:
    - Clearly state what assumption was made and why it matters
    - Show evidence found vs evidence NOT found
    - Explain impact on score if assumption is wrong
    - Ask a specific question for the human reviewer
    - Common critical assumptions: network ingress paths, public vs private access, authentication mechanisms, blast radius
    - Helper (writes files; use when needed): `python3 Scripts/Validate/update_validated_summaries.py --path Output/Findings/Cloud --in-place`
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
- When `Output/Knowledge/` is created or updated (including assumptions), immediately generate or update `Output/Summary/Cloud/Architecture_<Provider>.md`. Draw diagrams from the internet inwards (`flowchart TB`). Only include confirmed services unless the user explicitly requests assumptions. This is a standing rule throughout the session — do not wait until kickoff or session end.
- While writing/updating cloud findings, scan the finding content for implied **cloud services** (e.g., VM, NSG, Storage, Key Vault, AKS, SQL, App Service) and add them to `Output/Knowledge/` as **assumptions**, then immediately ask the user to confirm/deny.
- **Cloud resource native defaults:** When triaging findings about specific cloud resources, look up the **native provider default** for that resource type and note it in the finding:
  - **Azure examples:**
    - Storage Account: Public network access **enabled by default** (public endpoint)
    - Azure SQL Server: Public network access **enabled by default** (requires explicit firewall rules)
    - Key Vault: Public network access **enabled by default**
    - Cosmos DB: Public network access **enabled by default**
    - App Service: Public by default (unless deployed into VNET)
  - **AWS examples:**
    - S3 Bucket: Block public access **enabled by default** (as of Apr 2023)
    - RDS Instance: Public accessibility **disabled by default**
    - EKS Cluster: Public endpoint **enabled by default**
  - **GCP examples:**
    - Cloud Storage Bucket: Public access **disabled by default**
    - Cloud SQL: Public IP **disabled by default** (must opt-in)
  - Record these in `Output/Knowledge/<Provider>.md` under `## 🏗️ Cloud Resource Native Defaults` as you discover them during triage
  - Use this context to assess findings: "Finding shows public Storage Account. Azure Storage defaults to public - this is expected **unless** private endpoints are explicitly configured or IaC overrides the default."
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
- **Avoid running scripts/automations by default**. If you propose running a script (including repo utilities like `python3 Scripts/Utils/risk_register.py`), first explain:
  - what it does,
  - what files it will write/change,
  - why it’s necessary now.
  - **Exception:** during **repo scans**, it is OK (and preferred) to run `python3 Scripts/Scan/scan_repo_quick.py <abs-repo-path>` as the default initial skim.
  - **Exception (user-requested automation):** if the user asks for summaries to update automatically as new information becomes available, it is OK to run `python3 Scripts/Validate/update_validated_summaries.py --path Output/Findings/Cloud --in-place` after each material Q&A/knowledge update (it only removes title-only boilerplate when there is confirmed/applicability context).
  - **Exception (user-requested automation):** if the user asks for descriptions to stop repeating titles, it is OK to run `python3 Scripts/Utils/update_descriptions.py --path Output/Findings/Cloud --in-place` after bulk imports and/or as part of draft validation.
  - **Exception (user-requested automation):** if the user asks to adjust scores based on confirmed countermeasures and compounding, it is OK to run `python3 Scripts/Utils/adjust_finding_scores.py --path Output/Findings/Cloud --in-place` after material Q&A/knowledge updates (it only adjusts when the finding contains confirmed context and records the applied drivers under `### 📐 Rationale`).
  - **Exception (user-requested automation):** if the user asks for the risk register to auto-regenerate, it is OK to run a watcher in a separate terminal: `python3 Scripts/watch_risk_register.py` (or `--full` to also run the refresh helpers).
- **Automation language preference:** when automating a repo task, prefer **Python** over other
  languages to minimize extra dependencies the user may need to install.
- **Terminology Clarity:** When using security jargon, add inline explanation or glossary box for non-technical readers:
  - Common terms needing explanation: fails open/closed, blast radius, privilege escalation, lateral movement, defense-in-depth
  - Example glossary box format:
    ```markdown
    **Circuit Breaker Behavior Glossary:**
    - **Fails CLOSED** = Denies requests when downstream service is unavailable ✅ Secure
    - **Fails OPEN** = Allows requests through when downstream service is unavailable ❌ Insecure
    ```
  - Ensure findings are understandable to developers, platform engineers, and business stakeholders

## Outputs

- **Default behaviour:** outputs under `Output/Findings/`, `Output/Knowledge/`, and `Output/Summary/` are
  **generated per-user/session and are intentionally untracked** (see `.gitignore`).
  Change that only if you explicitly want to commit triage artifacts.
  - **File path references:** When referencing files within the Triage-Saurus repository (findings, knowledge, templates, agents), use **clickable markdown links with relative paths** from the current file location (e.g., `[Finding.md](../../Findings/Cloud/Finding.md)`, not inline code like `` `Output/Findings/Cloud/Finding.md` ``). External repo paths can remain as inline code.

- **Cloud findings:** `Output/Findings/Cloud/<RepoName>/<Titlecase>.md`
- **Code findings:** `Output/Findings/Code/<RepoName>/<Titlecase>.md`
- **IaC findings:** `Output/Findings/IaC/<RepoName>/<Titlecase>.md`
- **Secrets findings:** `Output/Findings/Secrets/<RepoName>/<Titlecase>.md`
  - **CRITICAL: All findings MUST be placed in a repository-specific subdirectory.**
  - **Note:** Repo scans that identify specific code-level security vulnerabilities (e.g., SQL injection, XSS, insecure deserialization) should extract those as individual findings under `Output/Findings/Code/` for tracking and remediation.
- **CRITICAL: One finding per file** — NEVER create consolidated finding files (e.g., `IaC_Findings.md` with all findings). Each security finding MUST be a separate markdown file:
  - Naming: `<RuleID_or_Title>_<RepoOrResource>.md` (e.g., `SQL_Firewall_All_Access_terraform-database.md`)
  - Each file follows the CloudFinding or CodeFinding template with TL;DR, Details, Risk, Recommendations, Metadata
  - This enables linking from architecture diagrams, risk registers, and cross-referencing
  - When delegating to sub-agents for rule scanning, explicitly instruct them to create individual finding files
- **Repo scan summaries:** `Output/Summary/Repos/<RepoName>.md` (one file per repo; follows `Templates/RepoFinding.md` structure with architecture diagram, security review, skeptic reviews, and metadata; use exact repo name without prefix)
  - **Thematic Summary:** The summary's `TL;DR` section MUST contain a high-level analysis grouped into common themes (e.g., "Critical Credential Mismanagement", "Pervasive Network Insecurity").
  - **Detailed Findings List:** The summary MUST include a complete list of all individual findings, grouped by the same themes. Each item must include the severity emoji, score, a link to the finding file, and be prioritized.
  - Should reference any extracted code findings using clickable markdown links under `## Compounding Findings` or in relevant finding summaries
  - **Cloud architecture knowledge:** When scanning a repo, any cloud architecture knowledge discovered (ingress paths, services used, authentication patterns, network topology) should be immediately captured in:
    - `Output/Knowledge/<Provider>.md` (confirmed services, controls, architecture facts)
    - `Output/Summary/Cloud/Architecture_<Provider>.md` (updated architecture diagrams)
- **Cloud summaries:**
  - Top-level architecture files only: `Output/Summary/Cloud/Architecture_*.md`
  - Provider-scoped resource summaries: `Output/Summary/Cloud/<Provider>/<ResourceType>.md` (see `Agents/CloudSummaryAgent.md`)
- **Risk register:** regenerate via `python3 Scripts/Utils/risk_register.py`
- **Optional bulk draft generator (titles → findings):** `python3 Scripts/Generate/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <input> --out-dir <output> [--update-knowledge]`
  - With `--update-knowledge`, it also generates provider-scoped cloud summaries under
    `Output/Summary/Cloud/<Provider>/`, regenerates `Output/Summary/Risk Register.xlsx`,
    and appends audit entries under `Output/Audit/`.

## After changes to findings
- **Risk register must stay current:** after creating or updating any finding, regenerate:
  - `python3 Scripts/Utils/risk_register.py` (updates `Output/Summary/Risk Register.xlsx`)
- If you need a quick, consistent score list (for summaries/architecture notes), run:
  - `python3 Scripts/extract_finding_scores.py Output/Findings/Cloud`
  - Output: a Markdown table to stdout (Finding link + **Overall Score** + description).

## Mermaid diagram validation (MANDATORY)
- **Web UI end-to-end validation is REQUIRED for diagram/icon changes** (not syntax-only checks):
  1. Render diagrams in a real browser flow (headless is acceptable) using the same UI path users take (`/api/diagrams/...` + `window._triage.renderDiagrams(...)`).
  2. Verify **each architecture tab/provider individually** (e.g., Alicloud, AWS, Azure, GCP, Oracle), not just the first tab.
  3. Capture screenshots of the loaded page/diagram panel for evidence (store in `/tmp` or session artifacts, not committed output).
  4. Check browser/runtime signals: Mermaid `error-text` nodes, console errors, and icon/static asset 4xx failures.
  5. Do not declare complete until browser-level checks show no render errors and no icon fetch failures for the tested scan.
- **After creating or updating any file with Mermaid diagrams** (findings, summaries, architecture diagrams, repo summaries), **ALWAYS run:**
  - `python3 Scripts/Validate/validate_markdown.py --path <path-to-file-or-directory>`
  - This validates Mermaid syntax and ensures **no `fill:` attributes** (which break dark themes)
- **Critical rule:** NEVER use `fill:#` in Mermaid style blocks. Use `stroke:` and `stroke-width:` instead.
  - ❌ `style node fill:#ff6b6b,stroke:#c92a2a` → ✅ `style node stroke:#c92a2a,stroke-width:3px`
  - ❌ `classDef error fill:#ffcccc` → ✅ `style node stroke:#ff0000,stroke-width:4px`
  - ❌ `[("text")]` for non-database nodes → ✅ `["text"]` for rectangles
- **When delegating diagram tasks to sub-agents:** Include the Mermaid styling rules in the prompt (no fill, stroke-only borders, correct node shapes). Sub-agents don't have visibility into these project rules.
- **Traffic Flow Standard (REQUIRED):** Use Mermaid `flowchart LR` diagrams for sequential traffic flows in repo summaries
  - ✅ Visualize request paths, authentication flows, data flows as Mermaid diagrams
  - ✅ Apply colored borders to show component types (security, network, identity, data)
  - ✅ Simple fan-out patterns (e.g., "APIM → 7 backends") can remain text-based lists
  - ❌ Long text arrow chains (`A → B → C → D → E → F`) are hard to scan - use Mermaid instead
- **Colored borders (REQUIRED for traffic flows, RECOMMENDED elsewhere):**
  - 🔴 Internet Edge (red): `#cc0000` stroke-width:2px - Internet/public ingress boundary
  - 🔵 Network boundary (blue): `#1971c2` stroke-width:2px - VNets, subnets, NSGs, firewalls
  - 🟢 Compute (green): `#5a9e5a` stroke-width:2px - App Service, AKS, VM, Functions
  - 🔵 Data Services (blue): `#4a90d9` stroke-width:2px - SQL, Storage, Redis, Cosmos DB
  - 🟠 Identity & Secrets (orange): `#e07b00` stroke-width:2px - Key Vault, AAD, managed identity
  - 🩵 Monitoring & Alerts (teal): `#2ab7a9` stroke-width:2px - Defender, logging, alerting
  - Use thicker or dashed borders only as an overlay for vulnerabilities/assumptions; base color still reflects resource category.
  - **Always include a legend** in diagrams explaining resource category border colors
  - **Legend format (inline, one line):** Place immediately after the Mermaid code block:
    ```markdown
    **Legend:** 🔴 Red = Internet Edge | 🔵 Blue = Network Boundary | 🟢 Green = Compute | 🔵 Blue = Data Services | 🟠 Orange = Identity & Secrets | 🩵 Teal = Monitoring
    ```
  - Place VNet/network assets inside a dedicated Mermaid subgraph and style that
    subgraph with stroke only (no fill), for example:
    `subgraph Network["🛡️ Network / VNet"] ... end` + `style Network stroke:#1971c2,stroke-width:2px`.
- **UTF-8 handling:** Emojis are acceptable in Mermaid diagrams (node labels AND subgraph labels)
  - ✅ **ALWAYS use edit/create tools** for files with emojis or Unicode characters
  - ❌ **NEVER use bash heredocs** (`cat << 'EOF'`) for UTF-8 content - causes Unicode corruption
  - Example corruption: `🔗` becomes `��` when using heredocs
- **Reference previous experiments:** Before generating architecture diagrams, check recent successful experiments (e.g., 015, 006) in `Output/Learning/experiments/` for expected format rather than generating from scratch.
- See `Agents/ArchitectureAgent.md` and `Agents/ContextDiscoveryAgent.md` for complete Mermaid styling rules.

## Sub-agent delegation requirements
When delegating tasks to sub-agents (via the `task` tool), include ALL relevant constraints in the prompt:
- **Output file structure:** One finding per file, naming conventions, target directories
- **Mermaid styling rules:** No fill, stroke-only borders, correct node shapes, inline legend
- **Template references:** Point to Templates/ files to follow (e.g., `Templates/CloudFinding.md`)
- **Project rules:** Sub-agents don't have visibility into Instructions.md or agent files
- **Validation:** Review sub-agent output against project standards before accepting

## Skeptic review updates
When DevSkeptic/PlatformSkeptic reviews are complete:
- **Update individual finding files** with severity adjustments and rationale (don't just create a separate Skeptic_Review.md)
- Add skeptic feedback under `## 🤔 Skeptic` section in each affected finding
- Update the finding's Overall Score to reflect the final reconciled score
- The Skeptic_Review.md can exist as a summary, but individual findings must be updated

## Utility scripts
- **Clear session artifacts (destructive):**
  - Dry-run: `python3 Scripts/clear_session.py`
  - Delete: `python3 Scripts/clear_session.py --yes`

- Ensure each finding includes:
  - `## 🗺️ Architecture Diagram` **directly under the title** (first section, before Overview)
  - `- **Overall Score:** <severity> <n>/10` **immediately after the diagram** (before Overview)
  - `## Meta Data` as the final section in the file
  - `- **Theme:** <ThemeName>`
  - `- 🗓️ **Last updated:** DD/MM/YYYY HH:MM`
  - **All finding types** (Cloud, Code, Repo) must include the Architecture Diagram section
