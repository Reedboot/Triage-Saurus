---
name: cloud-summary
description: Generate per-resource-type cloud summaries (Key Vault, Storage Account, AKS, etc.) and persist them to the DB for the web app after triage completes.
---

Generate cloud resource summaries grouped by resource type after Phase 5 (skeptic reviews) is complete.
Full agent guidance is in `Agents/CloudSummaryAgent.md`.

## Prerequisites
Phase 5 (skeptic reviews) must be complete and findings must have final scores. Verify:
```bash
python3 Scripts/Experiments/triage_experiment.py resume
```

## Generating a summary

Summaries are DB-first — persist each section via:

```bash
python3 Scripts/Persist/persist_section.py \
  --repo <RepoName> \
  --experiment <experiment_id> \
  --key <section_key> \
  --title "<Tab Label>" \
  --html "<section HTML>" \
  --generated-by CloudSummaryAgent
```

### Valid section keys
| Key | Tab label |
|---|---|
| `tldr` | TL;DR |
| `risks` | Risks |
| `architecture` | Architecture |
| `auth` | Authentication |
| `network` | Network |
| `ingress` | Ingress |
| `egress` | Egress |
| `containers` | Containers |
| `kubernetes` | Kubernetes |
| `cicd` | CI/CD |
| `dependencies` | Dependencies |

## Summary structure (per resource type)
Each summary must follow this format:
1. **Overall risk statement** — 2–3 sentences, highest-risk issue first
2. **Mermaid diagram** — `flowchart TB`, key interactions of the resource type; no `fill:` inline styles; use `<br/>` not `\n`
3. **Prioritised actions** — ordered by practical risk reduction (e.g., enable RBAC before network hardening if RBAC is the primary gap)
4. **`## Findings` section** — findings in priority order with severity emoji, label, score, and markdown link:
   ```
   - 🔴 **Critical 9/10:** [Public Blob with Credentials](../Findings/...)
   - 🟠 **High 7/10:** [Storage Account Without HTTPS Enforcement](../Findings/...)
   ```

## Rules
- **Do not rescore** — keep severity aligned with the referenced finding files.
- **Group by distinct resource type** — one summary per type (Storage Account, Key Vault, AKS, etc.).
- **Highlight highest-risk issues first**, then cluster related findings.
- **Reference source finding files explicitly** (markdown links, not backtick paths).
- Follow `Settings/Styling.md` for formatting.

## Validate after writing
```bash
python3 Scripts/Validate/validate_markdown.py --path Summary/Cloud/<Provider>
```

## Legacy file output (fallback)
If DB persistence fails, write to:
`Summary/Cloud/<Provider>/<ResourceType>.md` (e.g., `Summary/Cloud/AWS/EKS.md`)

Top-level `Summary/Cloud/` is for `Architecture_*.md` files only.

## Agent review logic
Full output format rules, section key registry, Mermaid diagram constraints:
`Agents/CloudSummaryAgent.md`
