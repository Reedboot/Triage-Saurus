# Context Discovery Agent

## Purpose
Fast, non-security repo discovery for architecture and scan scoping.

## Phase 1: automated baseline
Run:
`python3 Scripts/Context/discover_repo_context.py <repo_path> --repos-root <repos_root>`

This should identify:
- languages and frameworks
- IaC and orchestration
- container/runtime hints
- network and ingress patterns
- auth methods
- external dependencies
- route and backend mappings
- resource hierarchies
- Kubernetes risk signals

## Phase 2: deeper analysis
Use one explore agent to fill the gaps left by Phase 1.

Focus on:
1. purpose and business logic
2. request/traffic flow and middleware order
3. routing and architecture pattern
4. security controls and auth points
5. the 5-10 most important files

Do not run security scans during context discovery.

## Output
- `Output/Summary/Repos/<RepoName>.md`
- `Output/Knowledge/Repos.md`
- cloud architecture updates when IaC is detected

## Workflow
1. Run Phase 1.
2. Review the generated summary.
3. Run Phase 2.
4. Update repo and cloud summaries.
5. Ask the user for scan scope.

## Notes
- Keep Phase 2 concise.
- Prefer understanding over exhaustive searching.
- Add Kubernetes detail diagrams when a cluster is discovered.
