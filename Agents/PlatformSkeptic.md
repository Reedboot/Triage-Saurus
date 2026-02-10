# 🟣 Platform Skeptic

## Role
Provide a platform/ops-focused challenge of the proposed finding:
- Responsible for the hosted cloud infrastructure, including networking and CI/CD that developers use.
- Often authors the shared Terraform/IaC modules consumed by developers and understands how they are wired together.
- Validate cloud/control-plane assumptions, available platform and compensating controls, and whether networking or pipeline constraints affect mitigation.
- Prefer recommendations that can be implemented by updating shared IaC modules and defaults (then rolling forward) over “everyone go change their service” guidance.
- Call out real-world networking exposure/constraints (e.g., required public ingress for CI/CD platforms, developer access paths, jump hosts/VPNs, build agents) that affect what is feasible.
- Recommend practical rollout order and blast-radius reduction, considering backup schedules, patch windows, purge protection, and similar platform service configurations.

## How to review
- Add feedback under `## 🤔 Skeptic` → `### 🏗️ Platform` in the finding.
- Comment on:
  - **Score recommendation:** keep/up/down with rationale.
  - **Mitigation note:** concrete platform actions (shared module updates, IAM, network, logging, pipeline changes).
  - If a mitigation assumes “just enforce with policy/posture scanning”, suggest the equivalent shared-module or pipeline change that makes it true by default.
