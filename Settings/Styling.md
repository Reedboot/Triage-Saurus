# Styling Guidelines
This file documents the repository's styling conventions for code and documentation.

- **Filenames:** Follow the Titlecase convention (first letter uppercase, rest lowercase)
  as specified in Instructions.md.
- **Markdown:** Use 80-100 character soft wrap for paragraphs; use ATX-style headings
  (e.g., # Heading).
- **Code blocks:** Specify the language for fenced code blocks (```py, ```js, ```go).
- **Commits:** Use short imperative subject line and body; reference issue numbers when
  applicable.

Keep this file concise; expand with language-specific linters or formatter configs when
those tools are added to the repo.

## Additional MD Rules
- **Footer timestamp:** Only markdown files under findings/ require a "Last updated"
  timestamp in the footer using UK date and time (DD/MM/YYYY HH:MM with a colon).
  Seconds are optional and not required.
  - Put this inside `## Meta Data`, and keep `## Meta Data` as the **final section** in the file.
- **Headers:** Use headings to structure all markdown files; avoid long unheaded blocks.
- **Summary headers:** In `Summary/Cloud/`, use emoji-prefixed headers:
  `## 🧭 Overview`, `## 🚩 Risk`, `## ✅ Actions`, `## 📌 Findings`. Architecture
  summaries should use `## 📊 Service Risk Order` and `## 📝 Notes`.
- **Summary backlinks:** When referencing a finding from `Summary/`, use **clickable markdown links**
  with **human-readable link text** (the finding title) and **relative paths from the current file location**, not the file path, e.g.
  `[Public Network Access On Azure SQL Database Should Be Disabled](../../Findings/Cloud/Public_Network_Access_On_Azure_SQL_Database_Should_Be_Disabled.md)`.
  Avoid inline code backticks (`` `path/to/file.md` ``) as they are not clickable. This applies to all file references within the Triage-Saurus repository.
- **Compounding findings:** In the `## Compounding Findings` section, use clickable markdown links with relative paths, e.g., `[Related_Finding.md](../Cloud/Related_Finding.md)` or `[Other_Finding.md](../Code/Other_Finding.md)`.
- **Diagram placement:** In **all findings** (Cloud, Code, Repo), place `## 🗺️ Architecture Diagram` **immediately under the document title** (the `# 🟣 ...` line) as the **first section**. The `- **Overall Score:** ...` line must come **immediately after** the closing diagram fence (` ``` `), **before** any other section.
- **Security Review subheadings:** Prefer emoji-prefixed subheadings in findings:
  - `### 🧾 Summary`
  - `### ✅ Applicability`
  - `### 🎯 Exploitability`
  - `### 🚩 Risks` (repo findings)
  - `### 🔎 Key Evidence (deep dive)` (repo findings)
  - `### ✅ Recommendations`
  - `### 📐 Rationale`
- **Repo deep-dive evidence bullets:** In `Findings/Repo/`, prefix deep-dive evidence bullets with:
  - ✅ positive control/guardrail
  - ❌ weakness/risk signal
- **Bullet point colon formatting:** In bullet lists, when a line contains a colon (`:`),
  format the text left of the colon in bold. Example:
  - **Key:** value

- **Severity emoji bullets:** Use coloured emoji bullets for severity levels in
  documentation lists:
  - 🔴 Critical 8-10
  - 🟠 High 6-7
  - 🟡 Medium 4-5
  - 🟢 Low 1-3

- **Overall score:** Use a 1-10 scale where 10 is worst, and include a coloured
  circle plus severity label. Use this mapping:
  - 🔴 Critical 8-10
  - 🟠 High 6-7
  - 🟡 Medium 4-5
  - 🟢 Low 1-3
  - Example: `- **Overall Score:** 🔴 Critical 9/10`
- **Recommendations format:** In `## 🛡️ Security Review`, use checkbox bullets and
  include a per-recommendation downscore estimate with arrow emojis, e.g.,
  `- [ ] <recommendation> — ⬇️ <score>➡️<reduced-score> (est.)`. Use `0` when the
  recommendation is expected to remove the risk entirely.
- **Header bullet:** Only the document title should include a purple bullet
  point emoji (🟣) after the `#` and before the title text.
- **Title underscores:** Document titles must not include underscores `_` (e.g.,
  use `# 🟣 Architecture Azure`).
- **Review emoji:** Use consistent emoji prefixes for reviewer section headings.
  For cloud findings, use 🛠️ for `Dev Review` and 🏗️ for `Platform Review`.
- **Mermaid emoji:** Emojis are allowed in Mermaid diagrams if they do not break
  rendering. Use the following emoji consistently:
  
  **Infrastructure & Security:**
  - 🛡️ Security boundary/control/gateway
  - 🔐 Identity or authentication
  - 🔒 Internal/private component (VNet, private endpoint)
  - 🌐 Internet/public edge/external
  - 🚦 Traffic flow/routing/reverse proxy
  
  **Services & Components:**
  - 🗄️ Data store (database, storage)
  - 🧩 Service or component (generic)
  - 📡 API Gateway/API Management
  - ⚙️ Automation or pipeline
  - 🧑‍💻 User/operator
  
  **Monitoring & Observability:**
  - 📈 Monitoring/alerts/telemetry
  - 📊 Analytics/metrics/dashboards
  - 📋 Logging
  
  **Service Types (when specificity helps):**
  - 💰 Financial/accounts services
  - 💳 Payment services
  - 🔄 Synchronization/orchestration
  - ⚡ Real-time/streaming services
  - 💾 Storage/blob services
  
  **Flow & State:**
  - ✅ Success/valid/approved
  - ❌ Failure/invalid/rejected
  - ⚠️ Warning/caution
  - ⛔ Blocked/forbidden
  - 🎯 Target/destination/backend
- **Mermaid colors (theme-aware):** **Do not use `style fill`** in Mermaid diagrams.
  Background fill colors (e.g., `fill:#90EE90`, `fill:#FFB6C1`) break on dark themes.
  Use theme-neutral alternatives:
  - **Positive/secure components:** Use thicker borders (`stroke-width:3px`)
  - **Risk/exposure components:** Use dotted/dashed borders (`stroke-dasharray: 5 5`)
  - **Emphasis:** Use border styling (`stroke-width`, `stroke-dasharray`) or emojis from the standard set above
  - **Never use:** `style <node> fill:<color>` (breaks theme compatibility)

- **Mermaid subgraph standard:** Group nodes into category subgraphs with colour-coded
  stroke borders. This is the required structure for all architecture diagrams.

  | Group | Emoji | Border colour | Hex |
  |-------|-------|--------------|-----|
  | Internet Edge | 🌐 | Red | `#cc0000` |
  | Network boundary (VNet, NSG) | 🛡️ | Purple | `#8b5cf6` |
  | Compute (App Service, AKS, VM) | ⚙️ | Green | `#5a9e5a` |
  | Data Services (SQL, Storage, Redis) | 🗄️ | Blue | `#4a90d9` |
  | Identity & Secrets (Key Vault, AAD, MI) | 🔐 | Orange | `#e07b00` |
  | Monitoring & Alerts | 📈 | Teal | `#2ab7a9` |

  Apply via `style <SubgraphId> stroke:#hex,stroke-width:2px` — never on individual nodes
  unless highlighting a specific vulnerability (`stroke-dasharray: 5 5` or `stroke-width:3px`).

  **Node label format:** `Resource Type\n(friendly-name)` — type for context, real name
  for relatability. Example: `SQL Server\n(sql-terragoat)`.

  **Legend format** (use colour names, not hex codes):
  ```
  **Legend — group borders:**
  - 🔴 Red — Internet Edge (public attack surface)
  - 🟣 Purple — Network boundary (VNet, NSG, firewall controls)
  - 🟢 Green — Compute (App Service, AKS, VM, Functions)
  - 🔵 Blue — Data Services (SQL, Storage, Redis, CosmosDB)
  - 🟠 Orange — Identity & Secrets (Key Vault, Managed Identity, AAD)
  - 🩵 Teal — Monitoring & Alerts (Defender, Log Analytics)

  **Legend — arrows:**
  - Solid red arrow — direct internet-facing traffic (attack surface)
  - Solid arrow — internal routing / dependency
  - Dashed arrow — telemetry / secrets injection / policy enforcement
  ```

## Section Header Emoji Standards

Use consistent emoji for section headers across all documents:

**Finding Headers (all types - Cloud/Code/Repo):**
- `## 🗺️ Architecture Diagram` (first section after title)
- `## 🚦 Traffic Flow` (repo findings/summaries only)
- `## 🛡️ Security Review`
- `### 🧾 Summary`
- `### ✅ Applicability`
- `### 🎯 Exploitability`
- `### 🚩 Risks`
- `### 🔎 Key Evidence (deep dive)` (repo findings)
- `### ✅ Recommendations`
- `### 📐 Rationale`
- `## 🤔 Skeptic`
- `### 🛠️ Dev` (skeptic review)
- `### 🏗️ Platform` (skeptic review)
- `## 🤝 Collaboration`

**Summary Headers:**
- `## 🧭 Overview`
- `## 🚩 Risk`
- `## ✅ Actions`
- `## 📌 Findings`
- `## 📊 TL;DR - Executive Summary` (architecture summaries, when used)
- `## 📊 Service Risk Order` (architecture summaries)
- `## 📝 Notes`

**Knowledge Headers:**
- `## ✅ Confirmed`
- `## ❓ Assumptions`
- `## ❓ Open Questions` (or `## Unknowns`)
- `## 🛡️ Guardrails and Enforcement`
- `## 🌐 Network Exposure Defaults`

**Repo Summary Headers:**
- `## 🗺️ Architecture Diagram` (first section)
- `## 🚦 Traffic Flow`
- `## 🔍 Scan History`
- `## 🛡️ Security Observations`

Last updated: 05/02/2026 21:04
