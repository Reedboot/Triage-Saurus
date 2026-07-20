---
name: route-trace
description: Trace a public endpoint through App Gateway, APIM, and AKS using harvested subscription data.
---

Trace a route using the DB-backed trace API in `web/app.py`.

## Prerequisites

1. The web UI must be running:
   ```bash
   curl -sf http://127.0.0.1:9001 > /dev/null || echo "Server not running"
   ```
2. The subscription must have been harvested so `appgw_routing_rules`, `apim_api_routes`, `apim_backends`, and `aks_routes` exist.

## Usage

Trace an endpoint directly:

```bash
curl -s "http://127.0.0.1:9001/api/cloud/route-trace?sub=<subscription_id>&endpoint=https://events.mydomain.co.uk" | jq
```

Or, when the subscription id is already known:

```bash
curl -s "http://127.0.0.1:9001/api/subscriptions/<subscription_id>/trace-route?endpoint=https://events.mydomain.co.uk" | jq
```

## What it returns

- `resolved_chain` — the exact hop list, typically:
  `internet → listener → appgw → backend_pool → apim_service → apim_api → apim_backend → aks_ingress → aks_service → aks_deployment → aks_cluster`
- `mermaid` — a ready-to-render flowchart for the resolved route
- `matches` — candidate subscription/route matches when multiple routes exist

## When to use it

Use this skill when you want to:

1. Confirm the live hop chain for a specific hostname or URL.
2. Compare the traced route against the rendered Mermaid diagram.
3. Find missing hops, wrong backend targets, or diagram/harvest discrepancies.
4. Check whether APIM or AKS route data is missing from the harvest.

## Follow-up

If the returned chain does not match the rendered diagram, use:
- `subscription-diagram-check` to audit the UI rendering
- `diagram-review` to inspect the broader diagram generation pipeline

