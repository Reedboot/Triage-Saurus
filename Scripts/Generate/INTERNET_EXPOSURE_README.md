# Internet Exposure Detection for Architecture Diagrams

## Overview

Internet exposure detection automatically identifies resources that are accessible from the internet and visualizes them in architecture diagrams as connected to an "Internet" node. This transforms diagrams into **threat models** highlighting the attack surface.

The detection is **automatic** - you don't need to specify provider filters. The system:
- Auto-detects the cloud provider from resources in the diagram
- Runs detection for single-provider diagrams (AWS, Azure, GCP, OCI)
- Gracefully skips detection for mixed-provider or terraform-only diagrams
- Renders Internet nodes only when exposed resources are found

## What's Included

### Core Module: `internet_exposure_detector.py`

The `InternetExposureDetector` class uses **four detection methods** to identify internet-exposed resources:

1. **Explicit Findings** (🔴 Red #ff0000)
   - Query findings with `context_key='internet_exposure'` and `context_value='true'`
   - Highest confidence (high)
   - Represents confirmed security findings

2. **Firewall Rules Open to 0.0.0.0** (🟠 Orange #ff9900)
   - Detect SQL Server firewall rules with `start_ip_address='0.0.0.0'`
   - Detect NSG/Security Group rules allowing `0.0.0.0/0` to specific ports
   - High confidence (high)
   - Clear indicator of unrestricted access

3. **Resource Properties** (🟡 Yellow #ffff00)
   - Azure Storage: `public_access_enabled=true`
   - Azure SQL: `public_network_access_enabled=true`
   - AWS RDS: `publicly_accessible=true`
   - Medium confidence (medium)
   - Properties explicitly indicate public access

4. **Resource Type Heuristics** (🟡 Yellow #ffff00)
   - Resource types inherently public-facing by design
   - Azure: App Service, API Management, App Gateway, Front Door
   - AWS: ALB/NLB, API Gateway, CloudFront, ELB
   - GCP: Backend Service, API Gateway, Load Balancer
   - OCI: Load Balancer, API Gateway, CDN
   - Medium confidence (medium)
   - Based on resource nature, not explicit configuration

## Integration with Diagram Generation

### How It Works

1. **During `load_data()`**: `_detect_internet_exposure()` is called to analyze loaded resources
2. **Auto-detects provider** from the resources in the diagram
3. **Skips detection** if resources are from multiple providers or terraform-only
4. **Queries database** for findings and resource properties for the experiment
5. **Runs detector** for the detected provider
6. **Stores results** in `self.exposed_resources` dict
7. **During rendering**: `render_connections()` creates Internet → exposed_resource edges
8. **Color coding**: Connections styled by detection method confidence
9. **Conditional rendering**: Internet node only appears if exposed resources exist

### Key Features

- **Per-provider isolation**: Each provider (AWS, Azure, GCP, OCI) has its own Internet node in diagrams
- **Auto-detection**: No need to specify provider filters - system detects automatically
- **Graceful degradation**: Works with or without provider filter, handles mixed-provider diagrams
- **If detection fails**: Diagram still generates without Internet node (doesn't crash)
- **No breaking changes**: Works alongside existing connection detection

## Visualization Examples

### Single Exposed Resource
```
Internet → webapp-001 (App Service)
```
Color: Orange if firewall rule detected, Yellow if property-based, Red if explicit finding

### Multiple Exposures
```
Internet -.->|Firewall: 0.0.0.0/0| sql-server-001
Internet -.->|public_access_enabled=true| storage-001
Internet -.->|Resource type is API Gateway| api-mgt-001
```

## Provider-Specific Detection Rules

### Azure
- **Always exposed**: App Service, API Management, App Gateway, Front Door, Function App
- **Check firewall**: SQL Server (start_ip_address = 0.0.0.0)
- **Check properties**: 
  - `public_access_enabled` (Storage)
  - `public_network_access_enabled` (SQL, Cosmos)
  - `publicly_accessible` (Database)

### AWS
- **Always exposed**: ALB/NLB, API Gateway, CloudFront, ELB
- **Check firewall**: Security Groups with 0.0.0.0/0 on ports 22, 3306, 5432, etc.
- **Check properties**: 
  - `publicly_accessible` (RDS, Redshift)
  - `enable_public_network_access` (ElastiCache)

### GCP
- **Always exposed**: Cloud Load Balancer, API Gateway, Backend Services with public IPs
- **Check properties**: 
  - `enable_cdn` (Cloud Storage)
  - `public_ip_enabled` (Cloud SQL)

### OCI
- **Always exposed**: Load Balancer, API Gateway, CDN
- **Check rules**: Network Security Groups with 0.0.0.0/0

## Database Queries

The detector automatically runs these queries:

```sql
-- Query 1: Explicit internet_exposure findings
SELECT f.resource_id, f.finding_context
FROM findings f
WHERE f.experiment_id = ? AND f.finding_context IS NOT NULL

-- Query 2: Resource properties
SELECT resource_id, property_key, property_value
FROM resource_properties
WHERE resource_id IN (SELECT id FROM resources WHERE experiment_id = ?)
```

## Testing

### Run Tests
```bash
cd Scripts/Generate
python3 test_internet_exposure.py
```

### Test Coverage
- ✅ Individual detection methods (findings, firewall, properties, heuristics)
- ✅ Confidence ranking and merging
- ✅ Edge cases (invalid JSON, missing fields, private name overrides)
- ✅ Provider-specific rules (AWS, Azure, GCP, OCI)
- ✅ Color coding correctness
- ✅ No false positives on private resources

### Expected Output
```
✓ Findings-based detection works
✓ Firewall-based detection works
✓ Property-based detection works
✓ Heuristic-based detection works

✅ All basic tests passed!
```

## Customization

### Adding New Detection Methods

To add a new detection method, extend the `InternetExposureDetector` class:

```python
def _detect_by_custom_method(self, resources: List[Dict]) -> Dict[str, ExposureDetail]:
    """Custom detection method."""
    exposed = {}
    
    for resource in resources:
        # Your detection logic here
        if is_exposed_by_custom_method(resource):
            exposed[resource['resource_name']] = ExposureDetail(
                resource_name=resource['resource_name'],
                resource_id=resource['id'],
                exposure_type='custom',  # New type
                confidence='high',
                reason='Custom detection reason',
                color='#custom_color',
            )
    
    return exposed
```

Then call it in `detect_exposed_resources()`:
```python
exposed.update(self._detect_by_custom_method(resources))
```

### Adding Resource Types

Update `PUBLIC_BY_DESIGN` dict for each provider:

```python
PUBLIC_BY_DESIGN = {
    'aws': {
        # Add new types here
        'aws_new_service',
    },
    # ...
}
```

## Limitations & Edge Cases

### Known Limitations
1. **Requires valid property data**: Detector relies on resource properties being properly populated
2. **Heuristic is broad**: All ALBs/App Services marked exposed (no way to mark private except via name)
3. **Per-provider only**: Internet node only renders for provider with filter applied
4. **Connection labels**: May be truncated in Mermaid if too long

### Edge Cases Handled
- ✅ Resources with "private" in name won't match heuristic public types
- ✅ Private endpoints override property-based detection (if configured)
- ✅ Invalid JSON in properties handled gracefully
- ✅ Missing resource fields don't crash detector
- ✅ Multiple detection methods ranked by confidence

## Performance Considerations

- **Query cost**: Two database queries per diagram generation (findings + properties)
- **Processing**: O(n) for each detection method where n = number of resources
- **Memory**: Minimal (stores detection results in dict)
- **Impact**: <100ms additional processing on typical scans

## Troubleshooting

### No Internet node appears in diagram
**Possible causes**:
1. Provider filter not set (detection only runs per provider)
2. No exposed resources detected
3. Exposed resources not in emitted_nodes (filtered out earlier)

**Debug steps**:
```python
# Add to diagram generation
print(f"Exposed resources: {builder.exposed_resources}")
print(f"Emitted nodes: {builder.emitted_nodes}")
```

### Internet node missing connections
**Possible cause**: Resource names don't match between detection and rendering

**Debug steps**:
```python
for resource_name in exposed_resources:
    if resource_name not in emitted_nodes:
        print(f"Not emitted: {resource_name}")
```

## Future Enhancements

- [ ] UI control to toggle Internet node visibility
- [ ] Customizable detection rules per organization
- [ ] Risk scoring based on detection method confidence
- [ ] Export exposure report alongside diagrams
- [ ] Integration with compliance checkers
- [ ] Machine learning to identify false negatives

## Files Modified

- `Scripts/Generate/internet_exposure_detector.py` - Core detection module (new)
- `Scripts/Generate/test_internet_exposure.py` - Comprehensive test suite (new)
- `Scripts/Generate/generate_hierarchical_diagram.py` - Integration + rendering

## References

- Threat modeling: STRIDE, Attack surface analysis
- Cloud security: Well-Architected Framework (AWS, Azure, GCP)
- Internet exposure: OWASP Top 10, CWE-552 (Files/Directories Accessible to External Entities)

## Support

For questions or issues:
1. Check test suite for examples: `test_internet_exposure.py`
2. Review comments in `internet_exposure_detector.py`
3. Enable debug output in `generate_hierarchical_diagram.py`
