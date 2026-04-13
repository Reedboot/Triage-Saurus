# Resource Not Found Error Handling - Fix Summary

## Overview
Improved error handling and logging for "Resource not found" errors across the Triage-Saurus system, particularly for mermaid diagram generation and resource graph queries.

## Issues Addressed

1. **Silent Failures**: Diagram generation could fail silently without clear error messages
2. **Unclear Error Context**: Users didn't know what resources were available or why a query failed
3. **Missing Logging**: System errors weren't properly logged for debugging
4. **Poor Error Messages**: Generic "Resource not found" messages without helpful context

## Changes Made

### 1. **query_resource_graph.py** - Enhanced Error Messages
**File**: `Scripts/Persist/query_resource_graph.py`

**Changes**:
- Added detailed error messages showing:
  - What experiment ID was requested
  - What resource name was requested
  - What repository filter was applied (if any)
  - Hint to check spelling
- Improved from generic message to contextual error output

**Example Output**:
```
Resource not found for the requested experiment/repo.
  Experiment ID: 999
  Resource name: nonexistent-resource
  Repository: test-repo
  
Hint: Check that the experiment ID and resource name are spelled correctly.
```

### 2. **generate_diagram.py** - Resource Validation & Logging
**File**: `Scripts/Generate/generate_diagram.py`

**Changes**:
- Added logging support (import logging, setup logger)
- Enhanced `generate_blast_radius_diagram()` to:
  - Validate resource exists before processing
  - Raise `ValueError` with helpful context if not found
  - Show available resources (first 10) as suggestions
  - Log warnings when resources are not found
  
- Enhanced `generate_architecture_diagram()` to:
  - Log warnings when no resources are found for diagram generation
  - Include context (experiment_id, repo_name, provider) in logs

**Example Error Message**:
```
Resource not found: 'compromised-resource' in experiment 'exp-001'
Available resources: resource-1, resource-2, resource-3, ...
```

### 3. **db_helpers.py** - Enhanced Logging for Database Operations
**File**: `Scripts/Persist/db_helpers.py`

**Changes**:
- Added logging module and logger configuration
- Enhanced `get_cloud_diagrams()` function to:
  - Log debug messages when no diagrams are found
  - Log errors if database query fails
  - Include context (experiment_id, repo_name) in log messages
  - Gracefully handle exceptions by returning empty list

**Logging Output Examples**:
```
DEBUG: No diagrams found for experiment_id=001, repo_name=test-repo
ERROR: Failed to retrieve cloud diagrams for experiment_id=001, repo_name=test-repo: <error details>
```

### 4. **web/app.py** - API Error Handling
**File**: `web/app.py`

**Changes**:
- Enhanced `api_blast_radius()` endpoint to:
  - Distinguish between resource-not-found (404) and system errors (500)
  - Return appropriate HTTP status codes
  - Log validation errors as warnings (not full exceptions)
  - Provide user-friendly error messages

**API Response Examples**:
```json
// Resource not found (404)
{"error": "Resource not found: 'resource-x' in experiment 'exp-001'\nAvailable resources: ..."}

// System error (500)
{"error": "Failed to generate blast radius diagram: <details>"}
```

## Testing

All changes have been tested with:

1. **Non-existent Resources**: Proper error messages with available suggestions
2. **Non-existent Experiments**: Clear indication that no resources exist
3. **Valid Queries**: Existing functionality preserved
4. **JSON Output**: Still works correctly for programmatic access

### Test Commands

```bash
# Test with non-existent resource (shows helpful error)
python3 Scripts/Persist/query_resource_graph.py --experiment "999" --resource "nonexistent"

# Test with valid resource
python3 Scripts/Persist/query_resource_graph.py --experiment "001" --resource "Get-AzureVM"

# Test JSON output
python3 Scripts/Persist/query_resource_graph.py --experiment "001" --resource "Get-AzureVM" --json
```

## Logging Behavior

### Default Behavior
- WARNING level logging (shows errors and warnings only)
- Debug messages suppressed unless explicitly enabled

### Enable Debug Logging
```bash
PYTHONPATH=... python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
# Now run your code
"
```

## Backward Compatibility

All changes are backward compatible:
- Existing API responses unchanged for valid requests
- Error handling only affects error cases
- Script outputs remain identical for valid inputs
- JSON output format preserved

## Files Modified

1. `Scripts/Persist/query_resource_graph.py` - Enhanced error messages
2. `Scripts/Generate/generate_diagram.py` - Added logging and validation
3. `Scripts/Persist/db_helpers.py` - Added logging module and enhancements
4. `web/app.py` - Improved HTTP error handling and status codes

## Benefits

1. **Better User Experience**: Clear, actionable error messages
2. **Faster Debugging**: Helpful context in logs and error messages
3. **Better Error Distinction**: 404 vs 500 status codes
4. **Available Resources Suggestions**: Users see what they can query
5. **Audit Trail**: System logs all diagram generation attempts and failures

## Future Improvements

1. Add user-facing UI error messages based on these API errors
2. Add metrics/monitoring for resource-not-found errors
3. Consider caching available resources for faster suggestions
4. Add schema validation for resource names
