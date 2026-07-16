## Invocation & Output

**Trigger:** On-demand — invoke manually for a specific subscription when you want to audit its diagrams.

**Input:** `subscription_id` (required), `base_url` (optional, default `http://127.0.0.1:9000`)

**Output artifacts** (written under `Output/Audit/SubscriptionDiagramCheck_<sub_id>_<timestamp>/`):
- `report.md` — full findings report with pass/fail per check and recommended fixes
- `screenshots/` — Playwright screenshots of each diagram section
  - `ingress_diagram.png` — Internet Entry Points & Routing Flow
  - `rg_<name>.png` — per-Resource Group detail diagrams (first 5)
  - `assets_table.png` — Cloud Assets table view
- `icon_audit.json` — list of broken rendered icon references or failed icon loads
- `drilldown_smoke.json` — result of the node interaction smoke test
- `render_audit.json` — rendered node/edge counts and viewport validation
- `content_accuracy.json` — comparison of harvested counts vs rendered counts

**Full SKILL.md** (Copilot slash command): `.github/skills/subscription-diagram-check/SKILL.md`
**Route trace skill**: `.github/skills/route-trace/SKILL.md`

## Mermaid Diagram lookup map
- **Cloud Architecture UI:** `web/templates/cloud_architecture.html`
- **Mermaid page controller:** `web/static/js/cloud-architecture-mermaid.js`
- **Shared render helper:** `web/static/js/cloud-mermaid-helper.js`
- **Mermaid renderer wrapper:** `web/static/js/subscription-diagrams.js`
- **API route used by the mermaid page:** `web/app.py` (`/cloud/architecture` and `/api/cloud/architecture?sub=...&view=mermaid`)
- **Route trace API:** `web/app.py` (`/api/cloud/route-trace` and `/api/subscriptions/<sub_id>/trace-route`)
- **Mermaid generation:** `Scripts/Generate/generate_diagram.py`
- **Architecture bundle builder:** `Scripts/Generate/architecture_view_helpers.py`

---

# Cloud Subscription Diagram Validation Agent

## Role
Validate that the subscription-level diagrams rendered in the Triage-Saurus web UI **accurately and completely represent** the harvested Azure cloud assets for a given subscription. Use Playwright to capture real browser-rendered evidence from the React Flow UI.

## Core Principle
Every rendered node must correspond to a real harvested asset. Every public asset that should appear in the ingress view must be represented. Every icon must load. Every drillable node must respond to its supported interaction. Gaps in any of these are findings.

---

## Prerequisites

1. Web UI is running:
   ```bash
   curl -sf http://127.0.0.1:9000 > /dev/null || echo "NOT RUNNING"
   ```
2. Playwright is installed:
   ```bash
   python3 -m playwright install chromium
   ```
3. Harvest has completed for the subscription: assets exist in the `provisioned_assets` table

If the server is not running, instruct the user to start it, for example:

```bash
bash Scripts/start_web.sh
```

---

## Required Workflow

### Phase 1 — Navigate & Screenshot

1. Use Playwright to open `{base_url}/cloud` in a headless Chromium browser.
2. Find and click the row for `subscription_id` to load its diagrams.
3. Wait for the main React Flow diagram container to become visible.
4. Wait until nodes and edges are rendered and any loading indicators have cleared.
5. Fit the ingress diagram into the viewport if needed and save a screenshot as `screenshots/ingress_diagram.png`.
6. Expand the first 5 Resource Group sections and screenshot each rendered diagram as `screenshots/rg_<name>.png`.
7. Navigate to the cloud assets tab and save `screenshots/assets_table.png`.
8. If diagrams are clipped or partially off-screen, pan or zoom before capture. A clipped diagram is not valid evidence.

### Phase 2 — Rendered Icon Audit

9. Inspect rendered diagram nodes and identify icon elements used by the React Flow UI. This may include `<img>` elements, inline SVGs, background images, or icon wrapper containers.
10. For every rendered icon source that resolves to a URL, attempt retrieval from within the page context and record HTTP failures and load errors.
11. For icons that do not use URL-backed image tags, validate that the rendered icon is present and non-empty in the DOM.
12. Record broken, missing, or visually empty icons in `icon_audit.json`.
13. **Pass criteria:** Zero broken or missing rendered icons.

### Phase 3 — Drilldown Smoke Test

14. In the ingress diagram, identify the first drillable node using the current rendered UI markers, CSS classes, or data attributes.
15. Trigger the supported interaction for that node in the live UI. Use double-click if the current implementation expects double-click; otherwise use the actual interaction implemented by the UI.
16. Wait up to 3 seconds for the drilldown surface to appear, such as a modal, panel, drawer, or details card.
17. Record the result in `drilldown_smoke.json`, including:
    - node id or label
    - interaction attempted
    - pass/fail
    - visible content summary
18. **Pass criteria:** A visible drilldown surface appears within 3 seconds and corresponds to the selected node.

### Phase 4 — Content Accuracy Check

19. Query the harvest DB for the subscription:
    ```sql
    SELECT type, COUNT(*) AS n, SUM(is_public) AS public_n
    FROM provisioned_assets
    WHERE subscription_id = ?
    GROUP BY type
    ORDER BY n DESC
    ```
20. Compare the top 10 resource types and their public counts against what appears in the rendered diagrams and assets table.
21. Flag discrepancies where:
    - a resource type with `public_n > 0` has no corresponding rendered representation in the ingress diagram
    - total asset count shown in the assets table differs from DB count by more than 5%
    - rendered node counts materially diverge from harvested counts without an explicit grouping or collapsing rule
22. Write the comparison results to `content_accuracy.json`.

### Phase 5 — WAF & Listener Verification

23. For each Application Gateway represented in the ingress view, verify:
    - if `has_waf = True` in the DB, a WAF policy node or equivalent rendered representation is present
    - if `listeners` contains `HTTP:80`, an HTTP listener node is rendered
    - if `listeners` contains `HTTPS:443`, an HTTPS listener node is rendered
24. Confirm the expected relationships or edges are visible in the rendered graph.
25. Report any missing WAF or listener nodes as findings.

### Phase 6 — Exposure Accuracy Check

26. Query for assets marked as `is_public = 1`:
    ```sql
    SELECT name, type, fqdn
    FROM provisioned_assets
    WHERE subscription_id = ? AND is_public = 1
    ORDER BY type, name
    ```
27. For each public asset expected in the ingress view, check whether it appears as a visible public-exposure target in the rendered ingress diagram.
28. If the UI uses a red arrow or equivalent public-path treatment, verify that treatment is present.
29. Flag any public assets missing from the internet-exposure view.
30. For App Service and Function Apps, cross-check ASE ILB classification. If `hostingEnvironmentProfile` is set and the ASE is ILB, confirm `is_public = 0`.

### Phase 7 — SQL Asset Disambiguation Check

31. Query for SQL Databases:
    ```sql
    SELECT db.name AS db_name, srv.name AS server_name
    FROM provisioned_assets db
    JOIN provisioned_assets srv
      ON  srv.subscription_id = db.subscription_id
      AND srv.resource_group  = db.resource_group
      AND LOWER(srv.type)     = 'microsoft.sql/servers'
    WHERE db.subscription_id = ?
      AND LOWER(db.type)      = 'microsoft.sql/servers/databases'
    ORDER BY srv.name, db.name
    ```
32. For each database, confirm the assets table renders a parent server hint below or alongside the database name.
33. Flag databases that share a name across different servers but lack the hint.

---

## Output Format — `report.md`

```markdown
# Subscription Diagram Audit — <subscription_name> (<sub_id>)
**Date:** <timestamp>
**Base URL:** <base_url>

## Summary
| Check | Status | Details |
|---|---|---|
| Icon audit | PASS / FAIL | N broken or missing icons |
| Drilldown smoke | PASS / FAIL | Node X responded / No drillable node found |
| Content accuracy | PASS / WARN | N discrepancies |
| WAF/Listener nodes | PASS / FAIL | N missing nodes |
| Exposure accuracy | PASS / FAIL | N public assets missing from diagram |
| SQL disambiguation | PASS / WARN | N databases missing server hint |

## Findings

### [FAIL] Icon audit
- `<broken_path>` returned HTTP 404
- `<node_label>` rendered with an empty icon container

### [FAIL] Drilldown smoke test
- No drillable node was found in the rendered ingress diagram
- Possible cause: interaction handlers were not attached or drilldown metadata is missing
- Note the actual interaction used by the current UI and the exact element that failed

### [WARN] Content accuracy
- `microsoft.web/sites` has 12 public instances in DB but only 8 visible in the ingress diagram

### [FAIL] WAF/Listener nodes
- Application Gateway `<name>` has `has_waf=True` but no rendered WAF representation
- Application Gateway `<name>` has HTTP:80 listener but no rendered HTTP listener node

## Screenshots
- `screenshots/ingress_diagram.png`
- `screenshots/assets_table.png`
```

---

## Fixes for Common Findings

Diagnostics assume no specific UI controls exist. If data is incorrect, re-run the relevant harvest or ingestion pipeline directly.

| Finding | Likely Cause | Fix |
|---|---|---|
| Broken icons | Rendered icon path is wrong or the asset is missing | Check the icon resolution logic in the web app and verify the corresponding static icon assets exist at the expected paths |
| Drilldown fails | Interaction handlers not attached or node drilldown metadata missing | Check the React Flow node interaction wiring and verify drilldown metadata is populated for drillable nodes |
| WAF node missing | `has_waf` is false or incomplete in harvested data despite policy existing | Re-run the routing/WAF harvest pipeline or regenerate App Gateway metadata; verify `appgw_waf_policies` and `appgw_routing_rules` tables |
| HTTP listener missing | `listeners` field is null, incomplete, or not propagated to render logic | Re-run routing harvest logic or ingestion pipeline; verify `appgw_routing_rules` contains listener data |
| Public asset not in diagram | Exposure classification mismatch between harvest and render logic | Verify `is_public` from harvest is preserved during diagram build and not overridden by DNS resolution or runtime checks |
| App Gateway not in diagram | Gateway exposure classification missing or render filtering is too aggressive | Verify public exposure is seeded from routing data before any DNS-based downgrade or UI filtering |
| WAF mode null | Command or parser failed to capture WAF policy settings | Verify the WAF policy harvest logic captures both gateway-level and listener-level policy settings |
| Orphaned listener WAF policies | Per-listener firewall policy associations were not linked back to the gateway | Verify listener-level `firewallPolicy` references are included in gateway-to-policy mapping during harvest |
| ILB ASE marked public | ASE ILB classification data was missing or permissions blocked the lookup | Check the ASE harvest permissions and verify `internalLoadBalancingMode` is correctly classified |

---

## Implementation Notes

- Treat the browser-rendered React Flow UI as the source of truth.
- Do not mark a check as passed purely because backend data looks correct.
- Correct backend data with incorrect UI rendering is a failure.
- Screenshots are mandatory evidence for all diagram validation.
- If an element is virtualised or off-screen, bring it into view before deciding it is missing.
- Prefer user-visible validation over internal implementation assumptions.
