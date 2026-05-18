---
name: context-discovery
description: Run two-phase repo context discovery — automated baseline (Phase 1) then intelligent code analysis (Phase 2) — to build architecture diagrams and inform scan scope.
---

Run context discovery for one or more repositories before security scanning begins.
Full agent guidance is in `Agents/ContextDiscoveryAgent.md`.

## Prerequisites
- The target repository must be cloned under the repos root (default: `/mnt/c/Repos`).
- An experiment must exist. Check with:
  ```bash
  python3 Scripts/Experiments/triage_experiment.py resume
  ```
  If no experiment exists, create one first (see `experiment-run` skill).

## Phase 1 — Automated baseline (~10 seconds, no LLM)

```bash
python3 Scripts/Context/discover_repo_context.py <repo_path> \
  --repos-root /mnt/c/Repos \
  --output-dir Output/Learning/experiments/<id>_<name>
```

**Detects automatically:** languages, IaC, containers, network topology, hosting platform, CI/CD pipelines, API routes, auth methods, external dependencies, ingress patterns, parent-child resource hierarchies, and Kubernetes risk heuristics.

**Output:** `Output/Learning/experiments/<id>/Summary/Repos/<RepoName>.md` + DB rows in `resources` and `repositories` tables.

### After Phase 1 — regenerate diagrams
```bash
python3 Scripts/Generate/generate_diagram.py <experiment_id>
```

For AKS/EKS/GKE repos, this also generates:
`Output/Summary/Cloud/<Provider>/Architecture_<Provider>_Kubernetes_<ClusterName>.md`

## Phase 2 — Deeper code analysis (~30–60 seconds, no LLM)

```bash
python3 Scripts/Context/discover_code_context.py \
  --experiment <id> \
  --repo <repo_name> \
  --target <repo_path> \
  --output-dir Output/Learning/experiments/<id>_<name>
```

**Detects:** framework rules (from `Rules/Detection/Frameworks/`), auth patterns, container details, Kubernetes RBAC + workloads, CI/CD pipelines. Writes to `context_metadata` table (namespace: `phase2_code`).

### After Phase 2 — update diagrams again
```bash
python3 Scripts/Generate/generate_diagram.py <experiment_id>
```

## Key rules
- **Terraform module-defined infrastructure** = confirmed IaC intent (solid nodes in diagram).
- **Attack artifact paths** (`exploits/`, `attack/`, `poc/`, `CVE-*`) are ignored — detections are based on deployable config risk only.
- Phase 1 **MUST** use the `Rules/` catalog for pattern matching when opengrep is unavailable.
- Phase 2 runs **before** opengrep scanning (Phase 3) so detected languages/frameworks inform which misconfig rule folders to target.

## Output artifacts
```
Output/Learning/experiments/<id>_<name>/
  Summary/Repos/<RepoName>.md         ← architecture diagram + TL;DR + security observations
  Summary/Cloud/<Provider>/
    Architecture_<Provider>.md         ← cloud estate overview (multi-diagram)
    Architecture_<Provider>_Kubernetes_<ClusterName>.md  ← (if K8s detected)
```

## After context discovery
1. Run **architecture validation**: `architecture-validation` skill.
2. If validation passes, advance to Phase 3 via the `experiment-run` skill.

## Agent review logic
Two-phase approach details, Kubernetes risk heuristics, diagram generation rules:
`Agents/ContextDiscoveryAgent.md`
