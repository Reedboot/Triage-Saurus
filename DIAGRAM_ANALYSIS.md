# Triage-Saurus Diagram Generation Issues - Comprehensive Analysis

## Executive Summary

The architecture diagram generation has **three distinct issues**:

1. **VMs rendered outside parent networks** (Critical) - Incomplete hierarchy extraction
2. **NSG-VM relationships inferred but not nested** (Critical) - Inference incomplete  
3. **Terraform modules visible as assets** (Medium) - Missing seed data entry

---

## Issue #1: VMs Rendered Outside Parent Networks

### Root Cause

The hierarchy extraction uses only the `parent_resource_id` column, which doesn't establish subnet→VM relationships:

```sql
-- Lines 680-688: What gets extracted
SELECT parent.id, child.id 
FROM resources parent
JOIN resources child ON child.parent_resource_id = parent.id
WHERE experiment_id = ?
```

This query finds:
- ✓ VNet → Subnet (Subnet.parent_resource_id = VNet.id)  
- ✗ Subnet → VM (VM.parent_resource_id = NULL)
- ✗ NSG → VM (NSG.parent_resource_id = NULL)

The rendering layer then compounds this by forcing VMs to root level:

```python
# Line 1004: VMs extracted to filtered_roots
vms = [r for r in filtered_roots if _is_compute_tier_resource(r)]

# Line 1049: VMs rendered flat in compute_tier
for vm in vms:
    _render_resource_subgraph(vm, parent_children, ...)
```

### Current vs. Expected Behavior

**Current:**
```
subgraph vNet
  ├─ Subnet-1
  └─ Subnet-2
end
vm-1 ← WRONG: rendered outside VNet
```

**Expected:**
```
subgraph vNet
  ├─ Subnet-1
  │  └─ vm-1
  └─ Subnet-2
end
```

### Code Locations

| Component | Line(s) | Issue |
|-----------|---------|-------|
| Hierarchy extraction | 680-688 | Query doesn't find Subnet→VM |
| VM categorization | 1004 | VMs forced to filtered_roots |
| VM rendering | 1049 | VMs rendered flat in compute_tier |
| Subnet rendering | 1087-1091 | Subnet hierarchy doesn't include VMs |

### Status

**REQUIRES ARCHITECTURAL DECISION**
- Option A: Keep VMs at compute tier level (current, for clarity)
- Option B: Nest VMs inside subnets (for accuracy)

---

## Issue #2: NSG Containment Boundaries Not Established

### Root Cause

The NSG-VM inference (lines 873-911) **correctly traces the relationship** but only creates edges, not hierarchy:

```python
# Lines 883-911: NSG-VM inference
for assoc in nsg_associations:
    nic_id = assoc.parent_resource_id
    vm = find_parent_of_nic()
    
    # Creates edge:
    connections.append({
        'source': nsg['resource_name'],
        'target': vm['resource_name'],
        'connection_type': 'secures',  # ← Edge only
    })
    
    # ✗ MISSING: Update parent_children dict
    # if nsg['id'] not in parent_children:
    #     parent_children[nsg['id']] = []
    # parent_children[nsg['id']].append({'child_id': vm['id'], ...})
```

The rendering layer has NSG nesting logic but it's incomplete (lines 1059-1065):

```python
# Only works when len(nsgs) == 1
if nest_compute_in_nsg and len(nsgs) == 1:
    nsg = nsgs[0]
    # Wraps compute_tier but doesn't establish parent-child in parent_children dict
    lines.append(f"subgraph {nsg_id}[NSG: {nsg['resource_name']}]")
    _render_compute_tier(...)  # VMs rendered inside
    lines.append("end")
else:
    # When len(nsgs) != 1, NSG rendered as simple node
    _render_compute_tier(...)
    for nsg in nsgs:
        _emit_simple_node(nsg, indent)  # ← Not a subgraph!
```

### Current vs. Expected Behavior

**Current:**
```
subgraph vNet
  ├─ subgraph compute_tier
  │  └─ vm-1
  └─ nsg (simple node, not subgraph)
      
Edges: nsg --secures--> vm-1 (edge shown, no containment)
```

**Expected:**
```
subgraph vNet
  └─ subgraph nsg
      ├─ subgraph compute_tier
      │  └─ vm-1
end
```

### Code Locations

| Component | Line(s) | Issue |
|-----------|---------|-------|
| NSG-VM inference | 873-911 | Creates edges only, doesn't update parent_children |
| NSG nesting logic | 1059-1065 | Only works for single NSG, doesn't use parent_children |
| NSG rendering | 1069 | NSG rendered as simple node when multiple |

### Status

**FIXABLE** - Inference logic is correct, just needs to update parent_children dict and extend nesting logic.

**Estimated changes:** ~30-40 lines

---

## Issue #3: Terraform Modules Visible in Diagrams

### Root Cause

The `resource_type_db.py` seed data has no entry for `terraform_module`:

```python
# Lines 23-150+: _FALLBACK dict
# No entries found for:
#   - "terraform_module"
#   - "terraform_*_module" patterns
#   - Any module wrapper types
```

If the context extractor creates `terraform_module` resources, the auto-insertion logic defaults to:

```python
# When resource type not in seed data:
# get_resource_type() calls auto_insert_if_missing()
# which defaults display_on_architecture_chart = True
```

This causes modules to appear in the diagram as "Other" nodes (line 1017).

### Verification Query

```sql
SELECT resource_type, COUNT(*) FROM resources
WHERE resource_type LIKE '%module%'
GROUP BY resource_type;
```

If results exist, modules are being extracted and likely visible.

### Expected Fix

Add to `resource_type_db.py` (~line 25, after initial entries):

```python
"terraform_module": {
    "friendly_name": "Terraform Module",
    "category": "Other",
    "icon": "📦",
    "display_on_architecture_chart": False
},
```

### Status

**FIXABLE** - Single-line addition after verification that modules are being extracted.

**Estimated changes:** 1-2 lines (seed data entry)

---

## Data Flow Analysis

### Extraction → Rendering Pipeline

```
┌─────────────────────────────────────────┐
│ [1] Database (resources table)           │
│     - parent_resource_id column          │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ [2] Hierarchy Query (lines 680-688)      │
│     SELECT ... JOIN on parent_resource_id│
└─────────────┬───────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ [3] parent_children Dictionary           │
│     parent_id → [children]               │
│     Used by rendering layer for nesting  │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ [4] Inference Layer (lines 873-911, etc)│
│     ✓ Traces NSG→VM, Public IP parents  │
│     ✗ Creates edges only (Issue #2)     │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ [5] Rendering Layer (lines 1004-1117)   │
│     ✓ Uses parent_children for nesting  │
│     ✗ Can't create relations not in dict│
│     ✗ VMs forced to roots (Issue #1)    │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ [6] Mermaid Diagram                      │
│     Subgraph nesting reflects hierarchy  │
└─────────────────────────────────────────┘
```

### Key Architectural Insights

1. **Extraction is Limited**: Can only follow existing `parent_resource_id` relationships
2. **Inference is Incomplete**: Traces relationships but doesn't update the hierarchy dict
3. **Rendering is Rigid**: Can only nest using relationships in `parent_children` dict
4. **Design Decision Visible**: VMs intentionally kept at compute tier level for clarity

---

## Modification Roadmap

### Priority 1: Fix NSG Containment (URGENT)

**Why First**: Inference logic is 90% complete, just missing hierarchy update.

**Changes Needed**:

1. After NSG inference loop (after line 911), add:
```python
# Update parent_children with inferred NSG→VM hierarchy
if _vm_res:
    if _nsg['id'] not in parent_children:
        parent_children[_nsg['id']] = []
    parent_children[_nsg['id']].append({
        'parent_id': _nsg['id'],
        'parent_name': _nsg['resource_name'],
        'parent_type': _nsg['resource_type'],
        'child_id': _vm_res['id'],
        'child_name': _vm_res['resource_name'],
        'child_type': _vm_res['resource_type'],
    })
    child_ids.add(_vm_res['id'])
```

2. Modify lines 1059-1065 to handle multiple NSGs:
```python
# Check if any NSGs have children
nsgs_with_children = [nsg for nsg in nsgs if nsg['id'] in parent_children]

if nsgs_with_children:
    for nsg in nsgs_with_children:
        nsg_id = sanitize_id(nsg['resource_name'])
        lines.append(f"{indent}subgraph {nsg_id}[NSG: {nsg['resource_name']}]")
        # Render children of this NSG
        for child_entry in parent_children[nsg['id']]:
            child_res = {
                'id': child_entry['child_id'],
                'resource_name': child_entry['child_name'],
                'resource_type': child_entry['child_type'],
            }
            _render_resource_subgraph(child_res, parent_children, lines, 
                                     indent + "  ", _emitted_ids=_emitted_ids)
        lines.append(f"{indent}end")
else:
    _render_compute_tier(indent)
```

3. Filter VMs not to render twice:
```python
# Exclude VMs that are children of NSGs
nsg_vm_ids = {c['child_id'] for nsg in nsgs if nsg['id'] in parent_children
              for c in parent_children[nsg['id']]}
compute_vms = [vm for vm in vms if vm['id'] not in nsg_vm_ids]
```

**Test**: `test_single_nsg_is_rendered_as_compute_container_inside_vnet`

---

### Priority 2: VM Subnet Nesting (IMPORTANT - REQUIRES DECISION)

**Options**:

**Option A: Nest VMs in Subnets**
- Add subnet→VM inference after NSG inference
- Modify line 1004 to exclude subnet-nested VMs
- Update line 1087-1091 subnet rendering to include VMs

**Option B: Keep VMs at Compute Tier (Current)**
- Document architectural decision
- No code changes needed
- Clearer compute layer focus

**Recommendation**: Clarify desired architecture first.

---

### Priority 3: Hide Terraform Modules (QUICK WIN)

**In `resource_type_db.py`, add (~line 25)**:

```python
"terraform_module": {
    "friendly_name": "Terraform Module",
    "category": "Other",
    "icon": "📦",
    "display_on_architecture_chart": False
},
```

**Verification**:
```sql
SELECT resource_type, COUNT(*) FROM resources
WHERE resource_type LIKE '%module%'
GROUP BY resource_type;
```

---

## Summary Table

| Issue | Layer | Lines | Status | Fix Complexity |
|-------|-------|-------|--------|---|
| VMs outside networks | Rendering | 680-688, 1004, 1049 | Needs decision | Medium |
| NSG not parent | Both | 883-911, 1059-1065 | Fixable | Low |
| Modules visible | Extract | resource_type_db.py | Fixable | Very Low |

---

## Testing

### Existing Test Coverage

- `test_single_nsg_is_rendered_as_compute_container_inside_vnet` - Tests single NSG case
- `test_internal_zone_skipped_without_children` - Tests VM at root level
- `test_hidden_subnet_is_promoted_with_vm_child` - Tests subnet handling

### New Tests Needed

1. **Test: Multiple NSGs with VMs**
   - Verify each NSG becomes subgraph parent
   - Verify VMs nested inside respective NSGs

2. **Test: Subnet→VM Nesting** (if Option A chosen)
   - Verify VMs rendered inside subnets
   - Verify correct NSG/subnet/VM hierarchy

3. **Test: Terraform Module Visibility**
   - Verify modules don't appear in diagram
   - Query DB for module resources

