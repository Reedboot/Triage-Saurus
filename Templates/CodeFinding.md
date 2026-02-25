# 🟣 Cloud Finding Template
This document defines the layout for cloud security findings. For formatting
rules, follow `Settings/Styling.md`. For behavioural rules, follow
`Agents/Instructions.md`.

## Workflow Overview
1. **SecurityAgent** runs first, analyses the target, and outputs findings to
   a new file: `Findings/Cloud/Unprotected_Storage_Account.md`.
2. **SecurityAgent** updates `Knowledge/` with any new inferred/confirmed facts
   discovered while writing the finding (inferred facts must be marked as
   **assumptions** and user-verified).
3. **SecurityAgent** generates/updates the relevant architecture diagram under
   `Summary/Cloud/` based on the updated `Knowledge/` (assumptions = dotted border).
4. **Dev** and **Platform** review the findings, each appending their own
   sections under `## 🤔 Skeptic`.
5. **SecurityAgent** reconciles feedback, updates the final score, and appends
   the collaboration summary and metadata.

## Filename Conventions
- **Location:** All findings are stored in `Findings/Cloud/`.
- **Format:** `Findings/Cloud/Unprotected_Storage_Account.md` (use a
  short, Titlecase identifier).
- **Finding title:** Use a short, Titlecase identifier from the finding source
  (e.g., `Unprotected_Storage_Account`).

## File Template
```md
# 🟣 <finding-title>

## 🗺️ Architecture Diagram
```mermaid
flowchart TB
  Edge[Internet / Users] --> Svc[<cloud service>]
  Svc --> Data[<data store>]
  Svc --> Logs[Monitoring/Logs]

  Sec[Controls] -.-> Svc
```

**CRITICAL: Never use `style fill:<color>` in Mermaid diagrams** - breaks dark themes (Settings/Styling.md). Use standard emoji from Settings/Styling.md instead: 🛡️ 🔐 🔒 🌐 🚦 📡 🗄️ 📈 ✅ ❌ ⚠️ ⛔

- **Description:** <short description>
- **Overall Score:** <severity emoji + label> <score>/10 — *Final after skeptic review: Security X/10 → Dev [✅/⬇️/⬆️]Y/10 → Platform [✅/⬇️/⬆️]Z/10*
  - Note: Show score progression through skeptic reviews. Use ✅ if no change, ⬇️ if downgraded, ⬆️ if upgraded.
  - Example: `🟠 **7/10** (HIGH) — *Final: Security 9/10 → Dev ⬇️7/10 → Platform ✅7/10*`

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

## 🛡️ Security Review

### 🔍 Detection
- **Detected by:** `rule-id` or `manual`
- **Rule file:** `Rules/IaC/rule-name.yml` (if applicable)
- **Detection method:** <automated scan / manual review / external tool>

### 🧾 Summary
<brief business impact summary: what it means to the business if this isn’t fixed>

### ✅ Applicability
- **Status:** Yes / No / Don’t know
- **Evidence:** <what makes this true/false>

### ⚠️ Assumptions
- <assumption that could change score/applicability> (mark as Confirmed/Unconfirmed)

### 🔎 Key Evidence
- <evidence bullets with `path:line` references>

### 🎯 Exploitability
<exploitability>

### ✅ Recommendations
- [ ] <recommendation> — ⬇️ <score>➡️<reduced-score> (est.)

### 🧰 Considered Countermeasures
- 🔴 <countermeasure> — <effectiveness note>
- 🟡 <countermeasure> — <effectiveness note>
- 🟢 <countermeasure> — <effectiveness note>

### 📐 Rationale
<rationale>

## 🧪 Proof of Concept
**[Include this section for exploitable vulnerabilities]**

**Prerequisites:**
- [Access needed, tools required]

### Complete Test Script (Copy & Run)

```bash
#!/bin/bash
# [What this demonstrates]

# CONFIGURE YOUR ENVIRONMENT
ENDPOINT="https://[change-this]"

# [Steps with actual commands using real repo endpoints/files]
echo "=== Testing vulnerability ==="
[curl/commands]

echo "Expected: [HTTP status/behavior]"
```

### Verify Impact
[How to see the exploit worked - logs, database, behavior]

### Test the Fix
```bash
# After applying recommended fix
[Same commands - should now be blocked/secured]
```

**Expected after fix:** [Secure behavior]

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** <call out gaps, incorrect assumptions, or missing context>
- **Score recommendation:** ✅ Keep / ⬆️ Up / ⬇️ Down — *explicitly state why vs the Security Review score*.
- **How it could be worse:** <concrete escalation path, e.g., public endpoint + weak auth, lateral movement, data exfil>
- **Countermeasure effectiveness:** <which recommendation actually removes risk vs just reduces it; why>
- **Assumptions to validate:** <which assumptions would change applicability/score>

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** <call out gaps, incorrect assumptions, or missing context>
- **Service constraints checked:** <service doc/SKU/downtime/cost notes; include links if available>
- **Score recommendation:** ✅ Keep / ⬆️ Up / ⬇️ Down — *explicitly state why vs the Security Review score*.
- **Operational constraints:** <SKU/tier, network design, downtime, rollout sequencing>
- **Countermeasure effectiveness:** <coverage/drift risks; how to enforce/monitor at scale>
- **Assumptions to validate:** <which assumptions would change applicability/score>

## 🤝 Collaboration
- **Outcome:** <outcome>
- **Next step:** <next step>

## Compounding Findings
- **Compounds with:** <finding list or None identified>
  (use clickable markdown links with relative paths, e.g., `[Foo.md](../Cloud/Foo.md)` or `[Bar.md](../Code/Bar.md)`)

## Meta Data
<!-- Meta Data must remain the final section in the file. -->
- **Provider:** <Azure/AWS/GCP>
- **Resource Type:** <Key Vault/Storage Account/etc>
- **Source:** <Defender/Advisor/Scanner name>
- 🗓️ **Last updated:** DD/MM/YYYY HH:MM
```

## Required Sections
- 🛡️ Security Review
- 🤔 Skeptic
- 🤝 Collaboration
- Compounding Findings
- Meta Data

## Cross-Checks
- Always check existing findings to see if they compound the new issue.
- If they compound, state that clearly, review both issues, and add clickable links
  (e.g., `[Related_Finding.md](../Cloud/Related_Finding.md)`) in both `## Compounding Findings` sections.

## Testing
- Use the `sample/` directory for test runs and mock findings.
