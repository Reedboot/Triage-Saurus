---
name: subscription-diagram-check
description: On-demand Playwright audit of a subscription's rendered React Flow diagrams - verifies the UI matches harvested Azure reality using screenshots, DOM checks, interaction tests, and diagram-to-asset comparison.
---

Validate the subscription diagrams in the Triage-Saurus web UI following the workflow in `Agents/SubscriptionDiagramAgent.md`.

This skill audits the **actual rendered React Flow diagrams in the browser**, not backend diagram generation. Every check must be validated using Playwright and visual evidence.

## Prerequisites

1. Web UI reachable:
   curl -sf http://127.0.0.1:9001 > /dev/null || echo "Server not running"

2. Playwright installed:
   python3 -m playwright install chromium

3. Harvest completed for the subscription

If not running:
   bash Scripts/start_web.sh

## Inputs

- subscription_id (required)
- base_url (default http://127.0.0.1:9001)

## Audit Principles

- Browser-rendered UI is source of truth
- Screenshots are mandatory
- Do not trust backend without UI validation

## Phases

1. Navigate & Screenshot
- Load page /subscriptions/{subscription_id}
- Wait for .react-flow
- Capture ingress, RG (5), assets table

2. Icon Audit
- Validate rendered icons load
- Detect broken/empty icons

3. Drilldown Test
- Double-click node
- Confirm panel opens

4. Content Accuracy
- Compare node counts vs asset counts

5. WAF/Listener
- Confirm WAF + HTTP/HTTPS nodes exist

6. Exposure
- Confirm is_public nodes have visible path

7. SQL
- Ensure server name shown

## Output

Output/Audit/SubscriptionDiagramCheck_<sub_id>_<timestamp>/

Files:
- report.md
- screenshots/
- icon_audit.json
- drilldown_smoke.json
- render_audit.json
- content_accuracy.json
