# 🔍 Diagram Review Skill

## Role
Review generated architecture diagrams using browser-rendered evidence (Playwright screenshots) and structural checks from a **security architect** perspective.

## Core Rule
For each diagram element, answer: **why is it here and how does it support the threat model?**

If an element is unconnected, unnested, or unsupported by evidence, treat it as a strong detection smell and investigate whether:
- detection rules are missing (`Rules/Detection/*`)
- hierarchy extraction logic is incomplete
- diagram generation/parsing logic dropped relationships

## Required Workflow
1. Run baseline diagram validation with screenshots for each provider tab.
2. Review each issue with element-level rationale:
   - attack-path contribution (ingress, trust boundary, identity, data, control plane)
   - smell severity when connection/hierarchy is missing
3. Propose or update OpenGrep detection rules for confirmed gaps.
4. Validate each new/updated rule:
   - `opengrep scan --config <rule-file.yml> <target-repo>`
5. Re-run scan + diagram validation.
6. Produce before/after report with:
   - screenshots
   - issue deltas
   - rule changes + validation results
   - unresolved risks

## Hierarchy Expectations
- Child resources should be nested under logical parents (examples):
  - Storage Account → Container → Blob/Object
  - SQL Server/Instance → Database
  - API Management/API Gateway → APIs/Operations
  - Service Bus/Topic Namespace → Queues/Subscriptions

If children appear flat while parent context exists, flag as hierarchy smell.

## Noise Reduction Guidance
Call out resource types that likely should not appear on architecture diagrams when they do not contribute to threat modeling (e.g., pure metadata/config scaffolding). Keep rationale explicit and evidence-backed.
