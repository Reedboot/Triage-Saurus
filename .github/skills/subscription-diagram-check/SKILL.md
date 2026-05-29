---
name: subscription-diagram-check
description: On-demand Playwright audit of a subscription's rendered diagrams — checks icons, dblclick functionality, WAF/listener nodes, and that what's displayed matches harvested reality.
---

Validate the subscription diagrams in the Triage-Saurus web UI following the workflow in `Agents/SubscriptionDiagramAgent.md`.

## Prerequisites
Before running, verify:
1. The web UI is reachable: `curl -sf http://127.0.0.1:9000 > /dev/null || echo "Server not running"`
2. Playwright is installed: `python3 -m playwright install chromium`
3. A harvest has been completed for the target subscription.

If the server is not running, instruct the user to start it (e.g. `bash Scripts/start_web.sh`) before proceeding.

## Inputs

| Parameter | Required | Default | Description |
|---|---|---|---|
| `subscription_id` | ✅ | — | Azure subscription GUID or display name |
| `base_url` | ❌ | `http://127.0.0.1:9000` | Base URL of the Triage-Saurus web UI |

The user must supply `subscription_id`. Ask for it if not provided.

## Running the audit

Follow all 7 phases in `Agents/SubscriptionDiagramAgent.md` in order:

1. **Navigate & Screenshot** — Load the subscription page, screenshot ingress + RG diagrams + assets table
2. **Icon Audit** — Check every SVG `<image>` element for broken `href` references
3. **Drilldown Smoke Test** — Dispatch `dblclick` on first `node-drillable` node; verify panel opens
4. **Content Accuracy** — Compare DB asset counts vs. diagram node counts for top 10 resource types
5. **WAF & Listener Verification** — Confirm WAF policy nodes and HTTP/HTTPS listener nodes present
6. **Exposure Accuracy** — Confirm every `is_public=1` asset has a red-arrow path in ingress diagram
7. **SQL Disambiguation** — Confirm SQL Database rows show parent server name hint

## Output

Write all artifacts to `Output/Audit/SubscriptionDiagramCheck_<sub_id>_<timestamp>/`:

```
report.md                  ← full findings with summary table
screenshots/
  ingress_diagram.png      ← Internet Entry Points & Routing Flow
  rg_<name>.png            ← per-RG expanded diagrams (first 5)
  assets_table.png         ← cloud assets table
icon_audit.json            ← broken icon details
drilldown_smoke.json       ← smoke test result
```

After writing the report, show the user a brief summary of findings and the path to the full report.

## Key files

| File | Purpose |
|---|---|
| `Agents/SubscriptionDiagramAgent.md` | Full agent instructions and check definitions |
| `web/app.py` | `_build_ingress_diagram()` — generates ingress Mermaid |
| `web/templates/partials/subscriptions.html` | `_attachDrilldownHandlers()` — wires dblclick |
| `Scripts/Harvest/Azure/function_apps.py` | `fetch_ase_ilb_map()` — ILB ASE classification |
| `Scripts/Harvest/Azure/cosmos_db.py` | `_classify_exposure()` — CosmosDB public check |
