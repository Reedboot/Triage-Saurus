# Agents
This file provides a single discovery point for agent instructions in this
repository.

## Primary Instructions
- **Path:** Agents/Instructions.md
- **Purpose:** Canonical agent operating rules for this repo.
- **Note:** By default, the agent should avoid running `git` commands and avoid running scripts unless it explains why first (see `Agents/Instructions.md`).

## Additional Agent Files
- **Path:** Templates/CloudFinding.md
  - **Purpose:** Template and workflow guidance for cloud findings.
- **Path:** Templates/CodeFinding.md
  - **Purpose:** Template and workflow guidance for code findings.
- **Path:** Agents/ContextDiscoveryAgent.md
  - **Purpose:** Fast context discovery for repositories (purpose, tech stack, services, architecture) - runs before security scans.
- **Path:** Agents/RepoAgent.md
  - **Purpose:** Comprehensive guidance for repository scanning (ingress paths, architecture, dependencies, security).
- **Path:** Agents/DevSkeptic.md
  - **Purpose:** Review approach for developer-focused findings.
- **Path:** Agents/PlatformSkeptic.md
  - **Purpose:** Review approach for platform-focused findings.
- **Path:** Knowledge/DevSkeptic.md
  - **Purpose:** Reusable dev-centric context (app patterns, common mitigations, org conventions).
- **Path:** Knowledge/PlatformSkeptic.md
  - **Purpose:** Reusable platform-centric context (networking/CI/CD constraints, guardrails, rollout realities).
- **Path:** Agents/SecurityAgent.md
  - **Purpose:** Review approach for security-focused findings.
- **Path:** Agents/ArchitectureAgent.md
  - **Purpose:** Create and update cloud architecture diagrams based on knowledge.
- **Path:** Agents/CloudSummaryAgent.md
  - **Purpose:** Guidance for summarising cloud findings.
- **Path:** Agents/RepoSummaryAgent.md
  - **Purpose:** Create executive summaries for scanned repositories.
- **Path:** Agents/RiskRegisterAgent.md
  - **Purpose:** Create and maintain the executive risk register spreadsheet.
- **Path:** Agents/CodeSummaryAgent.md
- **Purpose:** Guidance for summarising code repository findings.
- **Path:** Agents/KnowledgeAgent.md
  - **Purpose:** Capture and maintain `Knowledge/` (confirmed + assumptions) and keep
    architecture diagrams in sync.
- **Path:** Agents/CloudContextAgent.md
  - **Purpose:** Gather foundational cloud environment context through structured survey before triage.
