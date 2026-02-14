# 🟣 {{title}}

## 🗺️ Architecture Diagram
```mermaid
{{architecture_mermaid}}
```

- **Description:** {{description}}
- **Overall Score:** {{overall_score_emoji}} {{overall_score_severity}} {{overall_score}}/10

## 🧭 Overview
{{overview_bullets}}

## 🛡️ Security Review
### 🧾 Summary
{{security_review_summary}}

### ✅ Applicability
- **Status:** {{applicability_status}}
- **Evidence:** {{applicability_evidence}}

### ⚠️ Assumptions
{{assumptions_bullets}}

### 🎯 Exploitability
{{exploitability}}

### 🚩 Risks
{{risks_bullets}}

### 🔎 Key Evidence (deep dive)
{{key_evidence_deep_bullets}}

### ✅ Recommendations
{{recommendations_checkboxes}}

### 🧰 Considered Countermeasures
{{countermeasures_bullets}}

### 📐 Rationale
{{rationale}}

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** <fill in>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **Mitigation note:** <note>

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** <fill in>
- **Service constraints checked:** <fill in: SKU/tier, downtime, cost>
- **Score recommendation:** ➡️ Keep/⬆️ Up/⬇️ Down — why vs Security Review.
- **Mitigation note:** <note>

## 🤝 Collaboration
- **Outcome:** Rendered from JSON model.
- **Next step:** Validate repo evidence and refine scoring as needed.

## Compounding Findings
- **Compounds with:** None identified

## Meta Data
<!-- Meta Data must remain the final section in the file. -->
- **Category:** {{category}}
- **Languages:** {{languages}}
- **Validation Status:** {{validation_status}}
- **Source:** {{source}}
- 🗓️ **Last updated:** {{last_updated}}
