# 🟣 Repo Scan Template

Use this template for **one file per scanned repo** under `Findings/Repo/` and **repo summaries** under `Summary/Repos/`.

## CRITICAL: Architecture Diagrams in Repo Summaries

**MANDATORY:** Every repo summary (`Summary/Repos/<RepoName>.md`) MUST include its own `## 🗺️ Architecture Diagram` as the FIRST section immediately under the document title.

**What to include:**
- **Request flow** as Mermaid diagram (not text) - show how requests traverse the application
- **Middleware/pipeline components** in execution order
- **Authentication/authorization flows**
- **Service dependencies** (databases, APIs, message queues)
- **Monitoring/logging integrations**
- **Internal application architecture** (not infrastructure - that's in Cloud/ diagrams)

**Style requirements:**
- Use `flowchart TB` (top-down) for request flows
- NO `style fill:<color>` directives (breaks dark themes - see Settings/Styling.md lines 79-85)
- Use emojis for visual clarity: 🛡️ 🔐 🗄️ 🌐 🧑‍💻 ⚙️ 📈
- Use dotted lines `-.->` for async/logging flows
- Use subgraphs for logical groupings (e.g., middleware pipeline)

## File Template
```md
# 🟣 Repo <repo-name>

## 🗺️ Architecture Diagram
```mermaid
flowchart TB
  client[👤 Client]
  
  subgraph app[" Application "]
    middleware1[Step 1]
    middleware2[Step 2]
    middleware3[Step 3]
  end
  
  backend[🗄️ Backend Service]
  logs[📊 Logging]
  
  client --> middleware1
  middleware1 --> middleware2
  middleware2 --> middleware3
  middleware3 --> backend
  middleware2 -.->|Async| logs
```

**CRITICAL: Never use `style fill:<color>` in Mermaid diagrams** - breaks dark themes (Settings/Styling.md lines 79-85). Use emojis instead: ✅ ❌ ⚠️ 🔴 🟡 🟢

- **Overall Score:** <severity emoji + label> <score>/10 — *Final after skeptic review: Security X/10 → Dev [✅/⬇️/⬆️]Y/10 → Platform [✅/⬇️/⬆️]Z/10*
  - Note: Show score progression through skeptic reviews. Use ✅ if no change, ⬇️ if downgraded, ⬆️ if upgraded.
  - Example: `🟠 **6/10** (HIGH - Moderate) — *Final: Security 7/10 → Dev ✅7/10 → Platform ⬇️6/10*`

## 📊 TL;DR - Executive Summary
*(Add this section after Collaboration is complete for quick reference)*

| Aspect | Value |
|--------|-------|
| **Final Score** | <emoji> **X/10** (Risk Level) |
| **Initial Score** | Security Review: X/10 |
| **Adjustments** | Dev: <✅/⬆️/⬇️> → Platform: <✅/⬆️/⬇️> |
| **Key Takeaway** | <one sentence summary of outcome> |

**Top 3 Actions:**
1. <Priority 1 with effort estimate>
2. <Priority 2 with effort estimate>
3. <Priority 3 with effort estimate>

**Material Risks:** <2-3 sentence summary>

**Why Score Changed:** <explain if Dev/Platform adjusted score>

---

## ❓ Validation Required
*(Include this section if there are critical assumptions that need user confirmation)*

**⚠️ <Assumption Topic> (UNCONFIRMED):**
<Description of what was assumed and why it matters>

- Evidence found: <what supports the assumption>
- Evidence NOT found: <what's missing>
- Impact on score: <how confirmation/rejection would change assessment>

**Please confirm:** <specific question for human reviewer>

---

## 🧭 Overview
<short overview of the repository purpose and key findings>

## 🛡️ Security Review
### Languages & Frameworks (extracted)
- <language/framework> — evidence: `<path>`

### 🧾 Summary
<short summary of material risks>

### ✅ Applicability
- **Status:** Yes / No / Don’t know
- **Evidence:** <what was observed>

### ⚠️ Assumptions
- <assumption that could change score/applicability> (mark as Confirmed/Unconfirmed)

### 🎯 Exploitability
<how an attacker would realistically leverage issues>

### 🚩 Risks
- <bullet list of notable risks/issues; link to related findings if they exist using clickable markdown links, e.g., `[Finding.md](../Cloud/Finding.md)`>

### 🔎 Key Evidence (deep dive)
Mark each deep-dive evidence item:
- 💡 = notable signal / observed component / in-use indicator (neutral)
- ✅ = observed guardrail / good practice / risk reducer (positive)
- ❌ = observed weakness / insecure default / risk increaser (negative)

For secret-like signals (password, token, etc):
- If used inside a module: check module code before flagging as ❌ (may be securely handled)
- If output is consumed by secure storage (e.g., Key Vault): flag as 💡 or ✅ depending on context
- Only flag as ❌ if cleartext exposure or insecure handling is confirmed

Examples:
- 💡 Terraform module in use — evidence: `modules/azure/resource.tf:42:module "firewall_rules" {`
- ✅ Secret stored via Key Vault module — evidence: `main.tf:15:module.key_vault.store_secret`
- ❌ Cleartext secret in pipeline variable — evidence: `pipeline.yml:20:ARM_CLIENT_SECRET=$plaintext`

### Follow-up tasks for repo owners (optional)
- [ ] <what to verify> — evidence/source to check: `<path>`

### Cloud Environment Implications
Capture any **reusable** cloud context inferred from the repo (IaC + app config).
Promote reusable facts into `Knowledge/` as **Confirmed**/**Assumptions**.

- **Provider(s) referenced:** <Azure/AWS/GCP>
- **Cloud resources/services deployed or referenced:** <Key Vault, Storage, AKS, ACR, SQL, ...>
- **Network posture patterns:** <public endpoints, private endpoints, etc>
- **Identity patterns:** <managed identity/workload identity/roles>
- **Guardrails:** <policy-as-code, module standards, CI checks>

### Service Dependencies (from config / connection strings)
Extract downstream dependencies indicated by configuration, e.g.:
- DBs: Postgres/MySQL/MSSQL/Cosmos
- Queues/streams: Service Bus/Event Hub/Kafka
- Logs/telemetry: App Insights/Log Analytics/Datadog/Splunk
- APIs: internal/external base URLs

- **Datastores:** <...>
- **Messaging:** <...>
- **Logging/Monitoring:** <...>
- **External APIs:** <...>

### Containers / Kubernetes (inferred)
- If `skaffold.yaml`, Helm charts, or Kubernetes manifests are present: assume **Kubernetes** deploy.
- If `Dockerfile`(s) are present: assume a **container registry** is in use.
- If multiple Dockerfiles/Helm charts exist: identify base images (`FROM ...`) and note supply-chain risks.

- **Kubernetes tooling found:** <skaffold/helm/kustomize/manifests>
- **Container build artifacts:** <Dockerfile paths>
- **Base images:** <list of FROM images>

### ✅ Recommendations
- [ ] <recommendation> — ⬇️ <score>➡️<reduced-score> (est.)

### 📐 Rationale
<why the score is what it is>

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** <call out gaps, incorrect assumptions, or missing context>
- **Score recommendation:** ✅ Keep / ⬆️ Up / ⬇️ Down — *explicitly state why vs the Security Review score*.
- **How it could be worse:** <realistic attacker path leveraging repo/app/IaC>
- **Countermeasure effectiveness:** <which recommendations measurably reduce risk; which are hygiene>
- **Assumptions to validate:** <which assumptions would change applicability/score>

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** <call out gaps, incorrect assumptions, or missing context>
- **Score recommendation:** ✅ Keep / ⬆️ Up / ⬇️ Down — *explicitly state why vs the Security Review score*.
- **Operational constraints:** <pipelines, auth to cloud, network reachability, rollout sequencing>
- **Countermeasure effectiveness:** <guardrail coverage, drift prevention, enforceability>
- **Assumptions to validate:** <which assumptions would change applicability/score>

## 🤝 Collaboration
- **Outcome:** <outcome>
- **Next step:** <next step>

## Compounding Findings
- **Compounds with:** <finding list or None identified>
  (use clickable markdown links with relative paths from Summary/Repos/, e.g., `[Foo.md](../../Findings/Cloud/Foo.md)`)

## Meta Data
<!-- Meta Data must remain the final section in the file. -->
- **Repo Name:** <repo-name>
- **Repo Path:** <absolute local path>
- **Repo URL:** <url or N/A>
- **Repo Type:** <Terraform/Go/Node.js/etc + purpose>
- **Languages/Frameworks:** <comma-separated list>
- **CI/CD:** <Azure Pipelines/GitHub Actions/etc or N/A>
- **Scan Scope:** <SAST / dependency (SCA) / secrets / IaC / All>
- **Scanner:** <tool name>
- 🗓️ **Last updated:** DD/MM/YYYY HH:MM
```
