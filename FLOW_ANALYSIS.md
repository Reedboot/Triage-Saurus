# Critical Data Flow Analysis - AzureGoat Experiment (001)

## Summary
Analysis of resource connections and data flows in the AzureGoat experiment to verify that essential data flows are properly represented in diagrams.

**Date:** April 13, 2026
**Experiment:** 001 (AzureGoat)
**Status:** ⚠️ PARTIALLY COMPLETE - Most critical flows present but key ingress paths missing

### Quick Status
| Flow | Status | Notes |
|------|--------|-------|
| Internet → Function App | ✅ PRESENT | Via _add_internet_connections(), exposed in diagram |
| Function App → DB | ✅ PRESENT | Data access connection rendered as dashed line |
| Function App → Storage | ✅ PRESENT | Data access connection rendered as dashed line |
| Internet → Public IP | ⚠️ PARTIAL | Connections created but not explicit arrows |
| Public IP → VM | ❌ MISSING | No connection in resource_connections table |
| VM → DB/Storage (via identity) | ❌ MISSING | Identity chain exists but not materialized |
| Automation Account → VM | ❌ MISSING | No explicit control path |

---

## 1. CRITICAL DATA FLOWS: PRESENT ✅

### 1.1 App → Database (Function App to Cosmos DB)
**Status:** ✅ COMPLETE - Both flows exist

- **Flow 1:** `function_app → db` (azurerm_function_app → azurerm_cosmosdb_account)
  - Connection ID: 25
  - Type: `data_access`
  - Protocol: Not specified
  - Data Flow ID: 1

- **Flow 2:** `function_app_front → db` (azurerm_function_app → azurerm_cosmosdb_account)
  - Connection ID: 26
  - Type: `data_access`
  - Protocol: Not specified
  - Data Flow ID: 2

### 1.2 App → Storage Account (Function App to Storage)
**Status:** ✅ COMPLETE - Both flows exist

- **Flow 1:** `function_app → storage_account` (azurerm_function_app → azurerm_storage_account)
  - Connection ID: 27
  - Type: `data_access`
  - Protocol: Not specified
  - Data Flow ID: 3

- **Flow 2:** `function_app_front → storage_account` (azurerm_function_app → azurerm_storage_account)
  - Connection ID: 28
  - Type: `data_access`
  - Protocol: Not specified
  - Data Flow ID: 4

---

## 2. CRITICAL DATA FLOWS: MISSING ❌

### 2.1 Internet → Public IP
**Status:** ⚠️ PARTIALLY PRESENT - Connections exist but are implicit

**Why it's critical:**
- Public IPs (VM_PublicIP, azurerm_public_ip.VM_PublicIP.name) are marked as `direct_exposure` with `is_entry_point=1` and `has_internet_path=1`
- These are entry points that should show "Internet → Public IP" connection

**Current state:**
- ✅ Public IP resources exist and are placed in Internet-Facing zone (IDs 22, 23)
- ✅ `_add_internet_connections()` CREATES these connections: Internet → VM_PublicIP, Internet → azurerm_public_ip.VM_PublicIP.name
- ✅ Connections are rendered in diagram (found in connections list)
- ⚠️ BUT connections are implicit (shown in Internet-Facing zone) rather than explicit arrows
- ✅ Exposure analysis correctly identifies them as entry points

**Root Cause:** Public IPs shown in zone but without outbound connections to VMs, so they appear as terminal nodes rather than gateways.

### 2.2 Public IP → Virtual Machine  
**Status:** ❌ MISSING - No connection in database

**Why it's critical:**
- The VM (dev-vm) is connected to a public IP but no connection tracks this relationship
- This is a critical ingress path: Internet → Public IP → VM

**Current state:**
- ✅ dev-vm (ID 26) exists
- ✅ VM_PublicIP (ID 22) exists
- ❌ No connection between them in resource_connections table
- ✅ Network interface (developerVMNetInt, ID 24) exists but is only in security group association
- ❌ Without this connection, diagram cannot show "Public IP → VM" path

### 2.3 Virtual Machine → Resources (VM access to DB/Storage)
**Status:** ❌ MISSING

**Why it's critical:**
- VM can access resources via its identity (user_id, az_role_assgn_vm)
- This creates a threat path: Internet → Public IP → VM → [DB, Storage, Automation Account]

**Current state:**
- dev-vm has `az_role_assgn_vm` (contains relationship)
- user_id has role assignments to DB and Automation Account
- But no direct data_access connections from dev-vm to these resources
- The identity chain exists but is not exposed as traversable data flows

### 2.4 Automation Account → VM
**Status:** ❌ MISSING (inverse relationship)

**Why it's critical:**
- Automation Account manages the VM (runbook access to VM resources)
- This is a control path: Automation Account → VM

**Current state:**
- Automation Account (ID 33) exists
- Runbook (ID 36) exists
- user_id has relationship to Automation Account
- But no explicit connection from Automation Account to VM

---

## 3. EXISTING CONNECTION ANALYSIS

### 3.1 Resource Hierarchy (contains relationships)
All properly captured:
- Storage Account → Containers, Blobs, Function Apps (6 connections)
- Function Apps → App Service Plan (2 contains)
- Role Assignments → Users/Identities (multiple contains)
- Network components properly hierarchical

### 3.2 Data Access Connections
Only capturing direct app-to-backend flows:
- function_app → db ✅
- function_app_front → db ✅
- function_app → storage_account ✅
- function_app_front → storage_account ✅

Not capturing:
- VM-based data access (via identity) ❌
- Automation Account control flows ❌

---

## 4. EXPOSURE ANALYSIS AUDIT

### 4.1 Entry Points Identified
- VM_PublicIP (ID 22): `is_entry_point=1`, `has_internet_path=1`, `exposure_level=direct_exposure`
- azurerm_public_ip.VM_PublicIP.name (ID 23): `is_entry_point=1`, `has_internet_path=1`, `exposure_level=direct_exposure`

**Issue:** Entry points identified but diagram does not show Internet→EntryPoint connections

### 4.2 Isolated Resources
All non-public resources marked as `isolated`:
- function_app: `exposure_level=isolated` ❌ SHOULD BE `mitigated` or `indirect_exposure` (behind public IP)
- storage_account: `exposure_level=isolated` ✅ CORRECT (no direct internet path)
- db: `exposure_level=isolated` ✅ CORRECT (no direct internet path)
- dev-vm: `exposure_level=isolated` ❌ SHOULD BE `mitigated` (behind public IP)

---

## 5. DIAGRAM GENERATION FUNCTION AUDIT

### 5.1 _add_internet_connections() Function
**Location:** Scripts/Generate/generate_diagram.py, line 321

**Current Logic:**
1. Queries findings for `metadata.internet_exposure=true`
2. Queries findings for `start_ip_address=0.0.0.0`
3. Queries resource_types for known internet-facing types (IGW, ELB, public IPs)
4. Queries exposure_analysis for `direct_exposure` or `mitigated` resources

**Issues Identified:**
1. **Finding-based detection:** No findings are being created that mark Public IPs as internet-exposed (findings table is empty or lacks the metadata)
2. **Type-based detection:** Should catch public IPs from resource_types, but needs verification
3. **Exposure-analysis fallback:** Has logic to check exposure_analysis, but may not be executing properly

**Test Results:**
- Public IPs marked in exposure_analysis as `direct_exposure` ✅
- But no "Internet" source connections appear in diagram ❌

---

## 6. THREAT MODEL COMPLETENESS

### Critical Threat Path: Internet → VM → Resources

**Current State:**
```
[MISSING] Internet
         ↓ [MISSING]
      Public IP (VM_PublicIP)
         ↓ [MISSING - Network Interface relationship]
      Virtual Machine (dev-vm)
         ↓ [MISSING - data_access from VM]
      ┌─────┴──────┐
      ↓            ↓
   Cosmos DB    Storage Account
      (isolated)    (isolated)
```

**Expected State:**
```
Internet (synthesized)
   ↓ [via Public IP entry point]
VM_PublicIP
   ↓ [associates via network interface]
dev-vm
   ├─→ Cosmos DB (via user_assigned_identity)
   ├─→ Storage Account (via user_assigned_identity)
   └─→ Automation Account (via role assignment)
```

---

## 7. ROOT CAUSE ANALYSIS

### Issue #1: Internet → Public IP Missing from Diagram ✅ PARTIALLY FIXED

**Root Cause:** Public IP resources are identified as internet-facing and placed in the Internet-Facing zone, but when there's NO connection from Public IP to other resources in the connections table, the connection rendering logic treats them as terminal nodes and collapses them.

**Status:** The code is partially working:
- ✅ Internet connections ARE being created by `_add_internet_connections()`
- ✅ Function Apps ARE showing Internet → function_app connections  
- ❌ Public IP → VM connection MISSING from connections table

**What Happens:** 
1. `_add_internet_connections()` successfully creates: Internet → VM_PublicIP, Internet → azurerm_public_ip.VM_PublicIP.name
2. The connection collapsing logic (line 1026) looks for connections involving public IPs
3. Since there's NO connection from VM_PublicIP TO dev-vm, the collapse doesn't happen
4. The Internet → PublicIP connections are rendered but as simple edges in the Internet-Facing zone

### Issue #2: Public IP ↔ VM Connection Missing

**Root Cause:** The network architecture shows a public IP resource, but no connection exists between the public IP and the dev-vm it's associated with.

**Current State:**
- VM_PublicIP resource exists (resource_id=22)
- dev-vm resource exists (resource_id=26)
- developerVMNetInt network interface exists (resource_id=24)
- But NO connections exist between them in resource_connections table

**Why It Matters:** 
- Threat model cannot show the complete path: Internet → Public IP → Network Interface → VM
- Without this connection, the diagram cannot show VM as reachable from Internet
- Blast radius analysis cannot trace from Internet through VM to its resources

### Issue #3: VM → Backend Access Missing

1. **Identity-based access not materialized as connections:**
   - dev-vm has user_assigned_identity (user_id, ID=32) with role assignments
   - user_id has role assignments to database and other resources
   - But no explicit data_access connections from dev-vm to these resources
   - Need to traverse: dev-vm → user_id → az_role_assgn_identity → [resource]

2. **Connection detection only looks at direct relationships:**
   - Only materialized connections (connection_type='data_access') are drawn
   - Inferred identity-based access is not materialized as connections

---

## 8. RECOMMENDATIONS & IMPLEMENTATION STATUS

### Phase 0: Bug Fixes ✅ COMPLETED
1. ✅ Fixed undefined `app_tier` variable in generate_diagram.py (line 671-681)
   - Added app_tier categorization for Function Apps and App Service Plans
   - Ensures application tier resources are properly rendered in their own zone

### Phase 1: Connection Materialization ⏳ TODO
1. **Materialize Public IP ↔ Network Interface → VM relationships**
   - Create connection_type='associates' from VM_PublicIP to developerVMNetInt
   - This will enable: Internet → PublicIP → NetworkInterface → VM path tracing

2. **Create data_access connections from VM → resources via identity chain**
   - Currently: VM → user_id (contains) → az_role_assgn_identity (contains) → resources
   - Should: VM → db (data_access via identity), VM → storage_account (data_access via identity)
   - This makes identity-based threat paths traceable in diagrams

3. **Create control_flow connections from Automation Account → VM**
   - Currently: user_id → Automation Account → (no VM connection)
   - Should: Automation Account → dev-vm (control_flow)

### Phase 2: Threat Model Enhancement ⏳ TODO
1. Update data flow detection to include identity-based access paths
2. Add Automation Account → managed resource flows  
3. Document why certain flows are legitimate or security-relevant
4. Update exposure_analysis to mark VM as 'mitigated' (not 'isolated') since it has internet path via public IP

---

## Verification Commands

```sql
-- Verify connections
SELECT COUNT(*) FROM resource_connections WHERE experiment_id = '001';
-- Result: 28 connections (see above)

-- Check for Internet connections
SELECT COUNT(*) FROM resource_connections 
WHERE experiment_id = '001' AND source_resource_id IN 
  (SELECT id FROM resources WHERE resource_name = 'Internet');
-- Result: 0 (MISSING)

-- Check public IPs
SELECT id, resource_name FROM resources 
WHERE experiment_id = '001' AND resource_type LIKE '%public_ip%';
-- Result: 2 public IPs

-- Check exposure analysis for entry points
SELECT COUNT(*) FROM exposure_analysis 
WHERE experiment_id = '001' AND is_entry_point = 1 AND has_internet_path = 1;
-- Result: 2 (public IPs correctly identified)
```

---

## Affected Diagram Features

1. **Diagram Title:** "AzureGoat Azure Resources (exp 001)"
2. **Expected Nodes:** Internet, Public IP, VM, Function Apps, DB, Storage
3. **Expected Edges:**
   - Internet → Public IP ❌ MISSING
   - Public IP → VM ❌ MISSING
   - VM → DB (via identity) ❌ MISSING
   - VM → Storage (via identity) ❌ MISSING
   - App → DB ✅ PRESENT
   - App → Storage ✅ PRESENT
   - Automation Account → VM ❌ MISSING
