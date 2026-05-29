# Platform Skeptic Knowledge

This file captures reusable platform-centric context for the PlatformSkeptic agent. Populate it with:

- **Networking / CI/CD constraints** — firewall rules, private endpoints, approved egress paths
- **Guardrails** — Azure Policy / SCP / org-level controls that block certain misconfigs automatically
- **Rollout realities** — phased deployments, canary regions, change-freeze windows

## Org-Specific Context

> **Not yet populated.** Fill this in for your organisation to improve PlatformSkeptic review accuracy.

## Cloud Provider Notes

> Add notes about your cloud setup (e.g., "All storage accounts have public-access-denied enforced by Azure Policy").

## Known Infrastructure Constraints

> Document infrastructure constraints that affect how findings should be scored (e.g., "Outbound internet access blocked at NSG level for all prod subnets").
