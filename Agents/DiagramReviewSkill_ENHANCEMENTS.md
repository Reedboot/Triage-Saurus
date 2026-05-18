# Diagram Review Skill - Enhancement Plan

**Status:** Design Ready  
**Priority:** HIGH (Skill currently misses rendering/asset validation)  
**Effort:** 2-3 days (4 new validation modules)

---

## Multi-Provider Support Status

✅ **COMPLETED** — Hashicorp/Terraform provider support added
- Added "hashicorp" provider detection to `_parse_provider_from_title()`
- Created icon directory structure: `/web/static/assets/icons/hashicorp/`
- Generated SVG icons for common Terraform resources:
  - **Provisioning:** terraform.svg
  - **Compute:** ec2.svg, auto-scaling.svg
  - **Networking:** vpc.svg, subnet.svg, load-balancer.svg
  - **Storage:** s3-bucket.svg
  - **Database:** rds.svg, dynamodb.svg
  - **Security:** security-group.svg, iam-user.svg
- Updated documentation in `DiagramReviewSkill.md` and `SKILL.md`

**Providers now supported:**
- Azure ✅
- AWS ✅
- GCP ✅
- Kubernetes ✅
- Oracle Cloud (OCI) ✅
- Alibaba Cloud ✅
- Hashicorp / Terraform ✅

---

## Problem Statement

The Diagram Review Skill successfully detects **structural issues** (orphans, hierarchy, parity gaps) but **completely misses**:
- ❌ Missing icon files
- ❌ Wrong icon mappings  
- ❌ Resources unable to render with visual identity
- ❌ Root causes for orphan nodes

**Result:** When user asks "Why no icon on EC2?", skill can't answer.

---

## Solution: Four Validation Modules

Each module addresses a specific validation gap. All can run in parallel during diagram analysis.

### ENHANCEMENT 1: Icon Availability Validation

**What it does:** Checks if icon SVG files exist for all detected resources.

**Implementation:**
```python
def validate_icon_availability(provider: str = 'aws') -> Dict:
    """Check if all mapped icon files exist on disk."""
    gaps = []
    for resource_type, (category, icon_name) in RESOURCE_TYPE_MAP.items():
        icon_path = ICONS_ROOT / provider / category / f'{icon_name}.svg'
        if not icon_path.exists():
            gaps.append({
                'issue_type': 'rendering_gap',
                'resource_type': resource_type,
                'severity': 'HIGH'
            })
    return gaps
```

**Output:** List of resources that can't render due to missing icons

**Detection Impact:** Would have caught all 4 missing AWSGoat icons immediately

**File to integrate into:** `Scripts/Validate/web_parallel_scan_validator.py` (new function)

---

### ENHANCEMENT 2: Icon Mapping Semantic Validation

**What it does:** Verifies that resource type → icon mappings are semantically correct.

**Implementation:**
```python
def validate_icon_mapping_semantics() -> Dict:
    """Check if icon mappings are semantically correct."""
    semantic_checks = [
        ('aws_route_table', 'route53', 'route-table', 
         'route53 is DNS, not routing'),
        ('aws_security_group', 'network-firewall', 'security-group',
         'network-firewall is different service'),
    ]
    
    errors = []
    for resource_type, wrong_icon, correct_icon, reason in semantic_checks:
        if MAPPING[resource_type][1] == wrong_icon:
            errors.append({
                'issue_type': 'mapping_error',
                'resource_type': resource_type,
                'wrong': wrong_icon,
                'should_be': correct_icon,
                'severity': 'CRITICAL'
            })
    return errors
```

**Output:** List of semantically incorrect mappings

**Detection Impact:** Would have caught route_table→route53 error immediately

**File to integrate into:** `Scripts/Generate/icon_resolver.py` (validation function)

---

### ENHANCEMENT 3: Rendering Pipeline Validation

**What it does:** For each orphan node, diagnoses root cause (rendering gap vs. real orphan).

**Implementation:**
```python
def validate_rendering_pipeline(orphan_nodes: List[str]) -> Dict:
    """Classify orphans by root cause."""
    diagnosis = {}
    
    for node_id in orphan_nodes:
        resource_type = infer_resource_type(node_id)
        
        # Check 1: Icon file exists?
        icon_exists = check_icon_exists(resource_type)
        
        # Check 2: Mapping is correct?
        mapping_ok = check_mapping_semantics(resource_type)
        
        # Classify
        if not icon_exists:
            root_cause = 'RENDERING_GAP'
        elif not mapping_ok:
            root_cause = 'MAPPING_ERROR'
        else:
            root_cause = 'REAL_ORPHAN'
        
        diagnosis[node_id] = {
            'root_cause': root_cause,
            'resource_type': resource_type,
            'severity': 'CRITICAL' if root_cause != 'REAL_ORPHAN' else 'INFO'
        }
    
    return diagnosis
```

**Output:** Classification of each orphan with root cause

**Detection Impact:** Would have reported "4 rendering gaps, 11 real orphans" instead of "15 orphans"

**File to integrate into:** `Scripts/Validate/web_parallel_scan_validator.py` (new function)

---

### ENHANCEMENT 4: Asset Validation Dashboard

**What it does:** Generates comprehensive asset status report.

**Implementation:**
```python
def generate_asset_validation_report(provider: str = 'aws') -> str:
    """Generate human-readable asset validation summary."""
    report = []
    
    # Icon availability
    gaps = validate_icon_availability(provider)
    report.append(f"\nIcon Coverage: {len(gaps)} missing icons")
    
    # Mapping semantics
    errors = validate_icon_mapping_semantics()
    report.append(f"Mapping Errors: {len(errors)} semantic mismatches")
    
    # Asset summary
    if gaps or errors:
        report.append("\n⚠️  Asset validation FAILED - rendering issues present")
    else:
        report.append("\n✓ Asset validation PASSED")
    
    return "\n".join(report)
```

**Output:** Formatted report showing icon coverage and mapping errors

**Detection Impact:** Provides visibility into system-wide asset gaps (334/342 AWS icons missing)

**File to integrate into:** `Scripts/Validate/review_generated_diagrams.py` (in summary generation)

---

## Integration Architecture

### Current Flow (Missing Rendering Validation)
```
Screenshot captured
    ↓
Orphans detected (connectivity analysis)
    ↓
Parity gaps detected (resource discovery)
    ↓
Hierarchy issues detected
    ↓
Report generated
    ✗ MISSING: Icon validation
    ✗ MISSING: Mapping validation
    ✗ MISSING: Root cause analysis
```

### Enhanced Flow (With Rendering Validation)
```
Screenshot captured
    ↓
Orphans detected (connectivity analysis)
    ↓
Parity gaps detected (resource discovery)
    ↓
Hierarchy issues detected
    ↓
[NEW] Icon availability validated
    ↓
[NEW] Icon mappings semantically validated
    ↓
[NEW] Orphan root causes classified
    ↓
[NEW] Asset validation report generated
    ↓
Report generated (with complete diagnostics)
```

---

## Code Integration Points

### File 1: `Scripts/Validate/web_parallel_scan_validator.py`

Add these two new functions after `find_orphan_nodes()`:

```python
def validate_icon_availability(provider: str = 'aws') -> list[dict]:
    """Check if icon SVG files exist for all detected resources."""
    # Implementation from Enhancement 1
    pass

def validate_rendering_pipeline(orphan_nodes: list[str], detected_resources: dict) -> dict:
    """Classify orphan nodes by root cause (rendering vs. real)."""
    # Implementation from Enhancement 3
    pass
```

Modify the diagram analysis function:

```python
def analyze_diagram(code: str, provider: str, ...) -> dict:
    # Existing code
    orphans = find_orphan_nodes(code)
    
    # NEW: Add rendering validation
    icon_gaps = validate_icon_availability(provider)
    orphan_diagnosis = validate_rendering_pipeline(orphans, inferred_resources)
    
    return {
        # Existing fields
        'orphan_issues': orphans,
        'parity_issues': parity,
        
        # NEW FIELDS
        'rendering_gaps': icon_gaps,
        'orphan_classification': orphan_diagnosis,
    }
```

### File 2: `Scripts/Generate/icon_resolver.py`

Add validation function:

```python
def validate_icon_mapping_semantics() -> list[dict]:
    """Check if icon mappings are semantically correct."""
    # Implementation from Enhancement 2
    pass
```

Call it during module initialization:

```python
# At module load time
_MAPPING_ERRORS = validate_icon_mapping_semantics()
if _MAPPING_ERRORS:
    logging.warning(f"Icon mapping errors detected: {_MAPPING_ERRORS}")
```

### File 3: `Scripts/Validate/review_generated_diagrams.py`

Add report generation to summary:

```python
def generate_diagram_review_report(...):
    # Existing code
    baseline_results = run_validation_pass(...)
    
    # NEW: Add asset validation to report
    asset_report = generate_asset_validation_report()
    
    report = f"""
    {existing_report}
    
    ## Asset Validation Results
    {asset_report}
    """
    
    return report
```

---

## Expected Outcomes

### Before Enhancements
```
Diagram Review Report:
  Orphan nodes: 15
  Parity gaps: 1
  Hierarchy issues: 1
  
  → No visibility into rendering problems
```

### After Enhancements
```
Diagram Review Report:
  Orphan nodes: 15
  
  Orphan Classification:
    - Rendering gaps: 4 (missing icons)
    - Mapping errors: 2 (wrong icons)
    - Real orphans: 9 (connectivity issues)
  
  Rendering Issues:
    - Missing icon files: 334/342 AWS resources
    - Icon mapping errors: 2 resources
  
  → Complete diagnostics and root cause analysis
```

---

## Testing Strategy

### Unit Tests

For each module:
```python
def test_icon_availability_validation():
    """Test detection of missing icons."""
    gaps = validate_icon_availability('aws')
    assert len(gaps) > 0, "Should detect missing icons"
    assert any(g['resource_type'] == 'aws_instance' for g in gaps), \
           "Should detect missing EC2 icon"

def test_icon_mapping_semantics():
    """Test detection of wrong mappings."""
    errors = validate_icon_mapping_semantics()
    # After fixes, should be 0 for core resources
    
def test_orphan_root_cause_classification():
    """Test classification of orphan nodes."""
    orphans = ['n5610', 'n5612']
    resources = {'n5610': 'aws_instance', 'n5612': 'aws_route_table'}
    diagnosis = validate_rendering_pipeline(orphans, resources)
    
    assert diagnosis['n5610']['root_cause'] in ['RENDERING_GAP', 'REAL_ORPHAN']
```

### Integration Tests

Test with real AWSGoat diagram:
```python
def test_awsgoat_diagram_review():
    """Test full flow with AWSGoat."""
    # Run diagram review with all validations
    results = analyze_diagram_with_validation(awsgoat_code, 'aws')
    
    # Should report 4 rendering gaps for goat_instance, goat_rt, sg, alb
    assert len([r for r in results if r['issue_type'] == 'rendering_gap']) >= 4
```

---

## Implementation Steps

### Phase 1: Core Validations (2 days)
- [ ] Implement Enhancement 1 (icon availability)
- [ ] Implement Enhancement 2 (mapping semantics)
- [ ] Implement Enhancement 3 (orphan classification)
- [ ] Write unit tests

### Phase 2: Integration (1 day)
- [ ] Integrate into web_parallel_scan_validator.py
- [ ] Integrate into icon_resolver.py
- [ ] Integrate into review_generated_diagrams.py
- [ ] Write integration tests

### Phase 3: Validation (1 day)
- [ ] Test with AWSGoat
- [ ] Verify catches all 4 rendering gaps
- [ ] Generate updated report
- [ ] Document findings

---

## Success Criteria

✅ Skill catches missing icon files  
✅ Skill catches wrong icon mappings  
✅ Skill classifies orphans by root cause  
✅ Report shows rendering gaps separately from real orphans  
✅ Asset validation dashboard visible in output  
✅ User questions like "Why no icon?" are answered automatically

---

## Long-Term Benefits

1. **Root Cause Analysis:** Every orphan has diagnosed cause
2. **Asset Visibility:** System-wide icon coverage dashboard
3. **Configuration Validation:** Semantic mapping errors caught
4. **Rendering Quality:** Diagrams guaranteed to render completely
5. **Faster Debugging:** Clear diagnostics when issues occur

---

## Risk Mitigation

**Risk:** Adding validations makes analysis slower  
**Mitigation:** All validations use cached file system checks, O(n) complexity

**Risk:** False positives in semantic validation  
**Mitigation:** Only check known semantic mismatches, whitelist approach

**Risk:** Icon assets missing system-wide (334/342)  
**Mitigation:** Report is informational, doesn't block diagram generation

---

## Files to Create/Modify

```
New code:
  └─ Scripts/Validate/rendering_validation.py (module with 4 functions)

Modified:
  ├─ Scripts/Validate/web_parallel_scan_validator.py (integrate 2 functions)
  ├─ Scripts/Generate/icon_resolver.py (integrate validation)
  └─ Scripts/Validate/review_generated_diagrams.py (integrate report)

Tests:
  └─ Scripts/Tests/test_rendering_validation.py (comprehensive tests)
```

---

## Estimated Effort

| Task | Effort | Dependencies |
|------|--------|--------------|
| Implement Enhancement 1 | 2 hours | - |
| Implement Enhancement 2 | 2 hours | - |
| Implement Enhancement 3 | 3 hours | Enhancements 1-2 |
| Implement Enhancement 4 | 1 hour | Enhancements 1-3 |
| Integration | 4 hours | All implementations |
| Testing | 4 hours | Integration complete |
| **Total** | **16 hours** | - |

---

## Success Example

**Before:**
```
User: "Why does goat_instance have no icon?"
Skill: *silence* (doesn't check icon files)
```

**After:**
```
User: "Why does goat_instance have no icon?"
Skill: "Icon file missing: aws/Arch_Compute/ec2.svg (RENDERING_GAP)
        This resource detected but cannot render. Create the icon or check mapping."
```

