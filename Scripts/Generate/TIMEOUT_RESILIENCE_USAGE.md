# Timeout Resilience for Internet Exposure Detection

## The Problem

If the AI scan times out during findings detection, partial findings might be lost before being saved to the database. This means some internet exposures won't be detected in the diagrams.

## The Solution

Use the `FindingsPersister` utility to ensure findings are committed to the database frequently, even if the overall scan times out.

## Quick Start

### In Your AI Scan Script

```python
from Scripts.Generate.findings_persister import FindingsPersister
import json

# Initialize persister (auto-commits every 100 findings)
persister = FindingsPersister(experiment_id='exp-001', batch_size=100)

# Scan resources
for resource in resources:
    # Check if resource is internet-exposed
    exposure_reason = check_internet_exposure(resource)
    
    if exposure_reason:
        # Add finding with proper context for diagram detection
        persister.add_finding(
            resource_id=resource['id'],
            finding_type='internet_exposure',
            severity='high',
            finding_context=json.dumps({
                'context_key': 'internet_exposure',
                'context_value': 'true',
                'reason': exposure_reason,  # e.g. "Firewall open to 0.0.0.0"
            })
        )

# Flush remaining findings on completion
persister.flush()
```

### How It Works

1. **Batch Buffering**: Findings are collected in memory (default batch size: 100)
2. **Auto-Commit**: Every 100 findings are automatically committed to the database
3. **On Timeout**: Partial findings already committed are preserved
4. **On Completion**: Remaining findings are flushed on exit

### Configuring Batch Size

For faster persistence (more frequent commits):
```python
# Commit every 10 findings instead of 100
persister = FindingsPersister(experiment_id='exp-001', batch_size=10)
```

For better performance (fewer commits):
```python
# Commit every 1000 findings
persister = FindingsPersister(experiment_id='exp-001', batch_size=1000)
```

## Using with Context Manager

Automatically flush on exit:

```python
with FindingsPersister(experiment_id='exp-001') as persister:
    for resource in resources:
        if is_exposed(resource):
            persister.add_finding(...)
    # Automatic flush on context exit
```

## Finding Context Format

The `finding_context` should be a JSON string with:
- `context_key`: Always 'internet_exposure' for exposure findings
- `context_value`: Always 'true' to indicate exposure
- `reason`: Human-readable explanation of exposure

```python
finding_context = json.dumps({
    'context_key': 'internet_exposure',
    'context_value': 'true',
    'reason': 'Firewall rule allows 0.0.0.0/0 on port 1433',
})
```

## Resumable Scans (Advanced)

For long-running scans, also use `ScanCheckpoint` to enable resuming from last position:

```python
from Scripts.Generate.findings_persister import FindingsPersister, ScanCheckpoint

checkpoint = ScanCheckpoint(experiment_id='exp-001')

# Get last checkpoint if resuming
last_cp = checkpoint.get_last_checkpoint()
start_index = last_cp.get('last_scanned_index', 0)

persister = FindingsPersister(experiment_id='exp-001', batch_size=100)

# Resume from checkpoint
for idx, resource in enumerate(resources[start_index:], start=start_index):
    if is_exposed(resource):
        persister.add_finding(...)
    
    # Save progress periodically
    if idx % 50 == 0:
        checkpoint.save_checkpoint({
            'last_scanned_index': idx,
            'resources_scanned': idx - start_index,
            'findings_count': persister.findings_count,
        })

persister.flush()
```

## Diagram Generation

The diagram generation automatically detects all persisted findings:

```python
# Findings are read from database
# Whatever was persisted (even partial) is used for detection
# Internet nodes appear for all detected exposures
builder = HierarchicalDiagramBuilder(experiment_id='exp-001')
diagram = builder.generate()  # Uses all persisted findings
```

## Benefits

✅ **Timeout Safety**: Partial findings aren't lost on timeout
✅ **Resumable**: Can resume from last checkpoint
✅ **Performance**: Configurable batch sizes for speed/responsiveness trade-off
✅ **Simple**: Easy to integrate into existing scan loops
✅ **Transparent**: Doesn't affect diagram detection logic

## Backward Compatibility

The `FindingsPersister` is completely optional:
- Existing scans continue to work as-is
- New scans can use it for timeout resilience
- Diagrams automatically use whatever findings exist in DB

## Troubleshooting

### Findings not persisting
- Check that `get_db_connection()` works in your scan environment
- Verify database write permissions
- Check logs for error messages

### Too many commits affecting performance
- Increase batch_size: `FindingsPersister(experiment_id, batch_size=500)`
- Commits are still faster than losing all findings on timeout

### Missing findings in diagrams
- Verify findings have correct `context_key='internet_exposure'`
- Check `finding_context` is valid JSON
- Run diagram generation to ensure detector reads all findings

## Example: Full AI Scan Integration

```python
import json
from Scripts.Generate.findings_persister import FindingsPersister

def run_ai_internet_exposure_scan(experiment_id, resources):
    """Scan resources for internet exposure with timeout resilience."""
    
    with FindingsPersister(experiment_id=experiment_id, batch_size=100) as persister:
        for resource in resources:
            try:
                # Check all 4 detection methods
                
                # 1. Check findings database
                if has_finding(resource, 'internet_exposure'):
                    persister.add_finding(
                        resource_id=resource['id'],
                        finding_type='internet_exposure',
                        severity='high',
                        finding_context=json.dumps({
                            'context_key': 'internet_exposure',
                            'context_value': 'true',
                            'reason': 'Explicit security finding',
                        })
                    )
                
                # 2. Check firewall rules
                elif has_open_firewall(resource, '0.0.0.0'):
                    persister.add_finding(
                        resource_id=resource['id'],
                        finding_type='internet_exposure',
                        severity='high',
                        finding_context=json.dumps({
                            'context_key': 'internet_exposure',
                            'context_value': 'true',
                            'reason': 'Firewall rule allows 0.0.0.0/0',
                        })
                    )
                
                # 3. Check properties
                elif has_public_property(resource):
                    persister.add_finding(
                        resource_id=resource['id'],
                        finding_type='internet_exposure',
                        severity='medium',
                        finding_context=json.dumps({
                            'context_key': 'internet_exposure',
                            'context_value': 'true',
                            'reason': 'public_access_enabled property',
                        })
                    )
                
            except Exception as e:
                print(f"Error scanning {resource['name']}: {e}")
                continue  # Continue with next resource
        
        # Automatic flush on exit
        print(f"Scan complete. {persister.findings_count} findings persisted.")
```

This ensures that even if the scan times out, all findings discovered up to that point are safely persisted.
