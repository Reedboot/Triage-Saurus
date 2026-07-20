---
name: diagram-review
description: Run Triage-Saurus diagram review (screenshots, threat-model smells, and before/after report).
---

Run the Triage-Saurus diagram review workflow for this repository.

## Prerequisites
Before running, verify:
1. The web UI is reachable: `curl -sf http://127.0.0.1:9001 > /dev/null || echo "Server not running"`
2. Playwright is installed: `python3 -m playwright install chromium`

If the server is not running, instruct the user to start it (e.g. `bash Scripts/start_web.sh`) before proceeding.

## Running the review

Use the orchestrator script with sensible defaults:
```bash
python3 Scripts/Validate/review_generated_diagrams.py --base-url http://127.0.0.1:9001
```

### Key flags
| Flag | Purpose |
|---|---|
| `--repos <name...>` | Limit to specific repo names |
| `--repo-at-a-time` | Strict mode: retry each repo before continuing |
| `--skip-after-pass` | Baseline-only run (no rule-apply + re-scan cycle) |
| `--only-unscanned` | Target repos with no prior scan history |
| `--concurrency <N>` | Parallel scan workers (default: 6) |
| `--scan-complete-timeout-sec <N>` | Per-repo scan timeout in seconds (default: 600) |
| `--opengrep-timeout-sec <N>` | OpenGrep validation timeout (default: 120) |
| `--audit-root <path>` | Override output root (default: `Output/Audit/`) |
| `--headed` | Show browser window (useful for debugging Playwright) |

## Screenshot capture for large diagrams
For larger diagrams that exceed viewport size, the tool automatically:
1. **Captures initial viewport** — Takes a screenshot of the visible canvas area
2. **Pans and captures systematically** — Scrolls horizontally and vertically to capture all diagram elements
3. **Assembles multiple screenshots** — Stores screenshots for each panned view to ensure complete coverage
4. **Documents element visibility** — Records which elements are visible in each screenshot

This ensures that even complex, multi-panel diagrams with dozens of resources are fully captured and can be reviewed element-by-element.

## Multi-Provider Support
The diagram review skill automatically detects and validates diagrams for multiple cloud and infrastructure providers:
- **AWS** — EC2, S3, RDS, VPC, Load Balancers, Security Groups, etc.
- **Azure** — App Services, Storage, SQL Database, API Management, etc.
- **GCP** — Compute Engine, Cloud Storage, Cloud SQL, VPCs, etc.
- **Kubernetes** — Pods, Services, Deployments, Namespaces, Ingress, etc.
- **Oracle Cloud (OCI)** — Compute, Object Storage, Autonomous Database, etc.
- **Alibaba Cloud** — ECS, OSS, RDS, VPC, etc.
- **Hashicorp / Terraform** — Infrastructure provisioning diagrams with IaC resource types

For each provider, the review validates hierarchy, icon availability, and icon mappings to ensure diagrams render correctly with appropriate visual identity.

## Output artifacts
All artifacts are written under:
```
Output/Audit/DiagramReviewSkill_<timestamp>/
  diagram_review_report.md   ← main before/after report
  baseline/                  ← baseline pass summaries + multi-view screenshots
    screenshots/             ← full set of panned/scrolled viewport captures
  after/                     ← after pass summaries + screenshots (unless --skip-after-pass)
    screenshots/             ← full set of panned/scrolled viewport captures
```

## Summarise results
After the run, report:
1. Path to `diagram_review_report.md`
2. Key before/after deltas (orphan issues, hierarchy smells, high-value smells, repos failed)
3. Any OpenGrep detection-rule validation failures (`detection_rule_validation_failed > 0`)
4. Any repos that timed out or failed

## Validation checks
The diagram review includes comprehensive validation for:
- **HTML entities in element names**: Checks for encoded HTML like `&gt;`, `&lt;`, `&br;`, `&nbsp;`, etc.
  These can cause rendering issues and are typically unintended encoding in diagram elements.
- **HTML tags**: Detects `<br>` tags and other inline HTML that may not render properly
- **Emoji + class suffix conflicts**: Flags problematic combinations that cause parser errors
- **Unbalanced brackets/braces**: Ensures proper bracket pairing
- **Invalid node IDs**: Validates node identifiers against Mermaid syntax rules
- **Empty subgraphs**: Detects Mermaid 11.x incompatible empty subgraph definitions
- **Reserved keywords**: Prevents use of reserved Mermaid keywords as IDs

## Agent review logic
For the element-level rationale, icon validation, hierarchy expectations, security posture checks,
and investigation checklist, follow the full guidance in `Agents/DiagramReviewSkill.md`.
