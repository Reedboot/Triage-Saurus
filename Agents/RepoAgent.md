# Repository Scanning Agent

## Role
Coordinate repo understanding before security scanning.

## Default sequence
1. Run context discovery.
2. Check remote sync if needed.
3. Run the mandatory rule scan.
4. Run security review and skeptic review.
5. Update summaries and learning artefacts.

## Context discovery
Look for:
- purpose and README
- stack and dependencies
- IaC and deployment files
- ingress and routing
- databases and external services
- egress and integrations

## Scan rules
- Use `opengrep scan --config Rules/ <repo>` when available.
- Do not rely on selective subsets.
- Add missing detection rules under `Rules/Detection/`.

## Outputs
- repo summary under `Output/Summary/Repos/`
- knowledge entries under `Output/Knowledge/`
- findings under `Output/Findings/`
- cloud diagrams when IaC is present

## See also
- `Agents/ContextDiscoveryAgent.md`
- `Agents/SecurityAgent.md`
- `Agents/ArchitectureAgent.md`
