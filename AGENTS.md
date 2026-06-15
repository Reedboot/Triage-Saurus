# Agents
This file provides a single discovery point for agent instructions in this
repository.

## Primary Instructions
- **Path:** Agents/Instructions.md
- **Purpose:** Canonical agent operating rules for this repo.
- **Note:** By default, the agent should avoid running `git` commands and avoid running scripts unless it explains why first (see `Agents/Instructions.md`). When opengrep is installed, all IaC/code scans must run `opengrep scan --config Rules/ <target>` and log the command. Manual grep fallbacks are not permitted — add missing detection rules under `Rules/Detection` instead.

## Session Management
- **Path:** SessionKickoff.md
  - **Purpose:** Session initialization flow and kickoff process.
- **Path:** Templates/Workflows.md
  - **Purpose:** Navigation flows, menu structures, and user journey definitions.

## Experiment & Learning Agents
- **Path:** Agents/ExperimentAgent.md
  - **Purpose:** Orchestrate triage experiments to optimise scan efficiency and accuracy.
  - **Slash command:** `.github/skills/experiment-run/SKILL.md`
- **Path:** Agents/LearningAgent.md
  - **Purpose:** Analyse experiment results and human feedback to improve future runs.
  - **Slash command:** `.github/skills/learning-promote/SKILL.md`

## Additional Agent Files
- **Path:** Templates/CloudFinding.md
  - **Purpose:** Template and workflow guidance for cloud findings.
- **Path:** Templates/CodeFinding.md
  - **Purpose:** Template and workflow guidance for code findings.
- **Path:** Agents/ContextDiscoveryAgent.md
  - **Purpose:** Fast context discovery for repositories (purpose, tech stack, services, architecture) - runs before security scans.
  - **Slash command:** `.github/skills/context-discovery/SKILL.md`
- **Path:** Agents/RepoAgent.md
  - **Purpose:** Comprehensive guidance for repository scanning (ingress paths, architecture, dependencies, security).
- **Path:** Agents/DevSkeptic.md
  - **Purpose:** Review approach for developer-focused findings.
- **Path:** Agents/PlatformSkeptic.md
  - **Purpose:** Review approach for platform-focused findings.
  - **Slash command:** `.github/skills/skeptic-review/SKILL.md` (combined Dev + Platform)
- **Path:** Agents/SecurityAgent.md
  - **Purpose:** Review approach for security-focused findings.
  - **Slash command:** `.github/skills/enrich-findings/SKILL.md`
- **Path:** Agents/ArchitectureAgent.md
  - **Purpose:** Create and update cloud architecture diagrams (multi-diagram views with hyperlinks between services).
- **Path:** Agents/DiagramReviewSkill.md
  - **Purpose:** Review generated diagrams via Playwright screenshots and structural analysis from a security-architect threat-model perspective.
  - **Slash command:** `.github/skills/diagram-review/SKILL.md`
- **Path:** Agents/ArchitectureValidationAgent.md
  - **Purpose:** Validate generated architecture diagrams for hierarchy issues, missing internet ingress, network segmentation gaps, and missing components. Runs after Phase 1, before security scanning.
  - **Slash command:** `.github/skills/architecture-validation/SKILL.md`
- **Path:** Agents/SubscriptionDiagramAgent.md
  - **Purpose:** On-demand Playwright audit of a subscription's rendered diagrams (icons, dblclick, WAF/listener nodes, content accuracy, exposure correctness).
  - **Slash command:** `.github/skills/subscription-diagram-check/SKILL.md`
- **Path:** Agents/CloudSummaryAgent.md
  - **Purpose:** Guidance for summarising cloud findings grouped by resource type.
  - **Slash command:** `.github/skills/cloud-summary/SKILL.md`
- **Path:** Agents/RiskRegisterAgent.md
  - **Purpose:** Create and maintain the executive risk register spreadsheet.
  - **Slash command:** `.github/skills/risk-register/SKILL.md`
- **Path:** Agents/CodeSummaryAgent.md
  - **Purpose:** Guidance for summarising code repository findings.
- **Path:** Agents/KnowledgeAgent.md
  - **Purpose:** Capture and maintain `Knowledge/` (confirmed + assumptions) and keep
    architecture diagrams in sync.
- **Path:** Agents/CloudContextAgent.md
  - **Purpose:** Gather foundational cloud environment context through structured survey before triage.
