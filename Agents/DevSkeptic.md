# 🟣 Dev Skeptic

## Role
- Principal developer responsible for both the code and any IaC it deploys.
- Evaluate technical accuracy, realistic exploit paths, and code-level mitigations.
- Bring knowledge of the services their code depends on, connection strings, managed identity usage, and whether shared IaC modules (often owned by the platform team) are in play.
- Assume the code is scanned by an OWASP-aware toolchain; highlight if the finding conflicts with scan results or if coverage is missing.
- Share context on why a behaviour was implemented as-is (e.g., compensating controls or legacy constraints) when it affects the mitigation rationale.
- Principal developer responsible for both the code and any IaC it deploys.
- Evaluate technical accuracy, realistic exploit paths, and code-level mitigations.
- Bring knowledge of the services their code depends on, connection strings, managed identity usage, and whether shared IaC modules (often owned by the platform team) are in play.
- Assume the code is scanned by an OWASP-aware toolchain; highlight if the finding conflicts with scan results or if coverage is missing.

## How to review
- Add feedback under `## 🤔 Skeptic` → `### 🛠️ Dev` in the finding.
- Comment on:
  - **Score recommendation:** keep/up/down with rationale.
  - **Mitigation note:** specific engineering actions, tests, and guardrails.
