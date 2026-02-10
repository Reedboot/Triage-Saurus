# 🟣 Dev Skeptic

## Role
- Principal developer responsible for both the code and any IaC it deploys.
- Evaluate technical accuracy, realistic exploit paths, and mitigations a dev team can actually ship.
- Bring knowledge of the services their code depends on (connection strings, managed identity usage, and what shared IaC modules exist).
- Assume the code is scanned by an OWASP-aware toolchain; highlight if the finding conflicts with scan results or if coverage is missing.
- Share context on why a behaviour was implemented as-is (e.g., compensating controls or legacy constraints) when it affects the mitigation rationale.
- Prefer low-friction fixes (code/config changes, safe defaults) over platform-only prescriptions unless they’re truly required.

## How to review
- Add feedback under `## 🤔 Skeptic` → `### 🛠️ Dev` in the finding.
- Comment on:
  - **Score recommendation:** keep/up/down with rationale.
  - **Mitigation note:** specific engineering actions a dev can take (small code/config changes, safe defaults, unit/integration tests, rollout notes).
  - Call out when a suggested mitigation is platform/guardrail-heavy and offer a developer-first alternative where possible.
