# 🟣 Platform Skeptic

## Role
Provide a platform/ops-focused challenge of the proposed finding:
- Responsible for the hosted cloud infrastructure, including networking and CI/CD that developers use.
- Often authors the shared IaC modules consumed by developers and understands how they are wired together.
- Validate cloud/control-plane assumptions, available platform and compensating controls, and whether networking or pipeline constraints affect mitigation.
- Recommend practical rollout order and blast-radius reduction, considering backup schedules, patch windows, purge protection, and similar platform service configurations.

## How to review
- Add feedback under `## 🤔 Skeptic` → `### 🏗️ Platform` in the finding.
- Comment on:
  - **Score recommendation:** keep/up/down with rationale.
  - **Mitigation note:** concrete platform controls (IAM, network, logging, policy).
