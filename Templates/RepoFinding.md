# 🟣 Repo {{ title }}

## 🗺️ Architecture Diagram
```mermaid
{{ architecture_mermaid }}
```

- **Description:** {{ description }}
- **Overall Score:** {{ overall_score.severity }} {{ overall_score.score }}/10

## 🧭 Overview
{% for bullet in overview_bullets %}
- {{ bullet }}
{% endfor %}

## 🛡️ Security Review
### 🧾 Summary
{{ security_review.summary }}

### ✅ Applicability
- **Status:** {{ security_review.applicability.status }}
- **Evidence:** {{ security_review.applicability.evidence }}

### ⚠️ Assumptions
{% for assumption in security_review.assumptions %}
- {{ assumption }}
{% endfor %}

### 🎯 Exploitability
{{ security_review.exploitability }}

### 🚩 Risks
{% for risk in security_review.risks %}
- {{ risk }}
{% endfor %}

### 🔎 Key Evidence (deep dive)
{% for evidence in security_review.key_evidence_deep %}
- {{ evidence }}
{% endfor %}

### ✅ Recommendations
{% for rec in security_review.recommendations %}
- [ ] {{ rec.text }} — ⬇️ {{ rec.score_from }}➡️{{ rec.score_to }} (est.)
{% endfor %}

### 📐 Rationale
{{ security_review.rationale }}

## 🤔 Skeptic
> Purpose: review the **Security Review** above, then add what a security engineer would miss on a first pass.

### 🛠️ Dev
- **What’s missing/wrong vs Security Review:** {{ skeptic.dev.missing }}
- **Score recommendation:** ➡️ {{ skeptic.dev.score_recommendation }}
- **How it could be worse:** {{ skeptic.dev.how_it_could_be_worse }}
- **Countermeasure effectiveness:** {{ skeptic.dev.countermeasure_effectiveness }}
- **Assumptions to validate:** {{ skeptic.dev.assumptions_to_validate }}

### 🏗️ Platform
- **What’s missing/wrong vs Security Review:** {{ skeptic.platform.missing }}
- **Score recommendation:** ➡️ {{ skeptic.platform.score_recommendation }}
- **Operational constraints:** {{ skeptic.platform.operational_constraints }}
- **Countermeasure effectiveness:** {{ skeptic.platform.countermeasure_effectiveness }}
- **Assumptions to validate:** {{ skeptic.platform.assumptions_to_validate }}

## 🤝 Collaboration
- **Outcome:** {{ collaboration.outcome }}
- **Next step:** {{ collaboration.next_step }}

## Compounding Findings
- **Compounds with:** {% for finding in compounding_findings %}{{ finding }}{% if not loop.last %}, {% endif %}{% endfor %}

## Meta Data
<!-- Meta Data must remain the final section in the file. -->
- 🗓️ **Last updated:** {{ meta.last_updated }}
