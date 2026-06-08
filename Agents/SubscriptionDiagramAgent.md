## Invocation & Output

**Trigger:** On-demand — invoke manually for a specific subscription when you want to audit its diagrams.

**Input:** `subscription_id` (required), `base_url` (optional, default `http://127.0.0.1:9000`)

**Output artifacts** (written under `Output/Audit/SubscriptionDiagramCheck_<sub_id>_<timestamp>/`):
- `report.md` — full findings report with pass/fail per check and recommended fixes
- `screenshots/` — Playwright screenshots of each diagram section
  - `ingress_diagram.png` — Internet Entry Points & Routing Flow
  - `rg_<name>.png` — per-Resource Group detail diagrams (first 5)
  - `assets_table.png` — Cloud Assets table view
- `icon_audit.json` — list of broken icon references found in rendered SVGs
- `drilldown_smoke.json` — result of the dblclick functionality smoke test

**Full SKILL.md** (Copilot slash command): `.github/skills/subscription-diagram-check/SKILL.md`

---

# 🔭 Cloud Subscription Diagram Validation Agent

## Role
Validate that the subscription-level diagrams rendered in the Triage-Saurus web UI **accurately and completely represent** the harvested Azure cloud assets for a given subscription. Uses Playwright to capture real browser-rendered evidence.

## Core Principle
Every node in the diagram must correspond to a real harvested asset. Every public asset must appear in the diagram. Every icon must load. Every drillable node must respond to double-click. Gaps in any of these = a finding.

---

## Prerequisites

1. Web UI is running: `curl -sf http://127.0.0.1:9000 > /dev/null || echo "NOT RUNNING"`
2. Playwright is installed: `python3 -m playwright install chromium`
3. Harvest has completed for the subscription: assets exist in `provisioned_assets` table

If the server is not running, instruct the user to start it (e.g. `bash Scripts/start_web.sh`).

---

## Required Workflow

### Phase 1 — Navigate & Screenshot

1. Use Playwright to open `{base_url}/cloud` in a headless Chromium browser.
2. Find and click the row for `subscription_id` to load its diagram.
3. Wait for `#subscription-diagram-container` to become visible.
4. Screenshot the full container: save as `screenshots/ingress_diagram.png`.
5. Expand the first 3 Resource Group sections and screenshot each: `screenshots/rg_<name>.png`.
6. Navigate to the cloud assets tab and screenshot: `screenshots/assets_table.png`.

### Phase 2 — Icon Audit

7. Query all `<image>` elements in every rendered SVG for their `href` attribute.
8. For each `href`, attempt `fetch(href)` from within the page context. Record 404s and errors.
9. Report broken icon count and paths in `icon_audit.json`.
10. **Pass criteria:** Zero broken icons.

### Phase 3 — Drilldown Smoke Test

11. In the ingress diagram SVG, find the first node with class `node-drillable` (these have the `⤵` badge).
12. Dispatch a `click` event on it using `node.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))`.
13. Wait up to 3 seconds for `#drilldown-modal` to become visible (i.e. `style.display !== 'none'`).
14. Record result (pass/fail + node id + drilldown modal content summary) in `drilldown_smoke.json`.
15. **Pass criteria:** Drilldown modal appears within 3 seconds.

### Phase 4 — Content Accuracy Check

16. Query the harvest DB for the subscription:
    ```sql
    SELECT type, COUNT(*) as n, SUM(is_public) as public_n
    FROM provisioned_assets WHERE subscription_id = ?
    GROUP BY type ORDER BY n DESC
    ```
17. Compare the top 10 resource types and their public counts against what appears in the diagram and assets table.
18. Flag discrepancies where:
    - A resource type with `public_n > 0` has no corresponding node in the ingress diagram
    - Total asset count in the table differs from DB count by more than 5%

### Phase 5 — WAF & Listener Verification

19. For each App Gateway in the ingress diagram, verify:
    - If `has_waf = True` in the DB, a `🛡️ WAF Policy` node should be present in the diagram
    - If `listeners` in the DB contains `HTTP:80`, a `🔴 HTTP:80` listener node should be present
    - If `listeners` in the DB contains `HTTPS:443`, a `🔒 HTTPS:443` listener node should be present
20. Report any missing WAF or listener nodes.

### Phase 6 — Exposure Accuracy Check

21. Query for assets that the harvest logic marks as `is_public = 1`:
    ```sql
    SELECT name, type, fqdn FROM provisioned_assets
    WHERE subscription_id = ? AND is_public = 1
    ORDER BY type, name
    ```
22. For each public asset, check if it appears as a **red-arrow** target in the ingress diagram (direct Internet → resource path).
23. Flag any public assets missing from the internet-exposure view.
24. For App Service / Function Apps: cross-check against ASE ILB classification — if `hostingEnvironmentProfile` is set and the ASE is ILB, confirm `is_public = 0`.

### Phase 7 — SQL Asset Disambiguation Check

25. Query for SQL Databases (data is in `provisioned_assets`, not `resources`):
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
26. For each database, confirm the assets table renders a parent server hint below the database name.
27. Flag databases that share a name across different servers but lack the hint.

---

## Output Format — `report.md`

```markdown
# Subscription Diagram Audit — <subscription_name> (<sub_id>)
**Date:** <timestamp>
**Base URL:** <base_url>

## Summary
| Check | Status | Details |
|---|---|---|
| Icon audit | ✅ PASS / ❌ FAIL | N broken icons |
| Drilldown smoke | ✅ PASS / ❌ FAIL | Node X responded / No drillable node found |
| Content accuracy | ✅ PASS / ⚠️ WARN | N discrepancies |
| WAF/Listener nodes | ✅ PASS / ❌ FAIL | N missing nodes |
| Exposure accuracy | ✅ PASS / ❌ FAIL | N public assets missing from diagram |
| SQL disambiguation | ✅ PASS / ⚠️ WARN | N databases missing server hint |

## Findings

### [FAIL] Icon audit
- `<broken_path>` returned HTTP 404
- ...

### [FAIL] Drilldown smoke test
- No node with class `node-drillable` found in ingress SVG
- Possible cause: `_attachDrilldownHandlers()` not called / node_drilldown_map empty
- Note: handler uses `click` event (not `dblclick`); modal element is `#drilldown-modal` (not `#drilldown-panel`)

### [WARN] Content accuracy
- `microsoft.web/sites` has 12 public instances in DB but only 8 visible in diagram

### [FAIL] WAF/Listener nodes
- App Gateway `<name>` has `has_waf=True` but no `🛡️ WAF Policy` node in diagram
- App Gateway `<name>` has HTTP:80 listener but no `🔴 HTTP:80` node

## Screenshots
- ![Ingress Diagram](screenshots/ingress_diagram.png)
- ![Assets Table](screenshots/assets_table.png)
```

---

## Fixes for Common Findings

| Finding | Likely Cause | Fix |
|---|---|---|
| Broken icons | SVG `<img src>` path 404 | Check `_get_icon_path()` in `web/app.py` against actual `web/static/assets/icons/` paths |
| Drilldown fails | `_attachDrilldownHandlers` not called or `node_drilldown_map` empty | Check `subscriptions.html` render flow + `_build_ingress_diagram` node registration |
| WAF node missing | `has_waf` is False in DB despite policy existing | Re-run harvest with `🔄 Refresh WAF & Routing` button; check `appgw_waf_policies` table |
| HTTP listener missing | `listeners` field null in `provisioned_assets` | Re-run routing harvest; verify `appgw_routing_rules` table for this gateway |
| Public asset not in diagram | DNS failure in web server env overriding `is_public=1` from harvest | Fixed in `_build_ingress_diagram`: `harvest_is_public` flag prevents DNS downgrade of confirmed-public resources |
| App Gateway not in diagram | `is_public=0` despite public routing rules | Fixed: `public_appgw_names` seeded from `appgw_routing_rules.exposure_level='Public'` before DNS check |
| WAF mode NULL | `waf-policy show` failed; list stub also missing `policySettings` | Fixed in `harvest_routing`: tries list stub properties as fallback; also captures per-listener policy associations |
| Orphaned per-listener WAF policies | `associated_gateways=[]` because only gateway-level policy was indexed | Fixed in `harvest_routing`: per-listener `firewallPolicy` refs now populate `gw_waf_map` |
| ILB ASE marked public | `fetch_ase_ilb_map()` returned empty (permissions) | Check `az appservice ase list` permissions; verify ASE `internalLoadBalancingMode` |
