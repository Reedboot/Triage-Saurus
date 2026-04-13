# Tier Detection Improvements - Summary of Changes

## File Modified
`Scripts/Generate/generate_diagram.py`

## Changes Made

### 1. Added Application Tier Detection Function (Lines 665-679)
New helper function to identify application-tier resources:
```python
def _is_application_tier_resource(r: dict) -> bool:
    """Return True if resource is an application-tier resource"""
```

**Detects:**
- `app_service_plan`, `service_plan`
- `function_app`, `linux_function_app`, `windows_function_app`
- `app_service`, `linux_web_app`, `windows_web_app`
- `elastic_beanstalk`

### 2. Updated Resource Grouping Logic (Lines 686-687)
- Extract `app_tier` resources separately using the new detection function
- Prevent `app_tier` resources from being grouped as regular VMs:
  - `app_tier = [r for r in filtered_roots if _is_application_tier_resource(r)]`
  - `vms = [...not _is_application_tier_resource(r)...]`

### 3. Updated Internet-Facing Filter (Line 699)
- Filter `app_tier` from internet-facing resources
- Maintains separation across all resource categories

### 4. Added Application Tier Subgraph Rendering (Lines 800-814)
New dedicated section in diagram generation:
- Located between Internet-Facing and Data Tier zones
- Uses emoji ⚙️ for visual identification
- Full label: "⚙️ Application Tier"
- Supports parent-child hierarchies:
  - app_service_plan contains child function_apps
  - Uses `_render_resource_subgraph()` for nested rendering

## Behavior Changes

### Before
- App Service Plans and Function Apps grouped with VMs in Compute tier
- No visual distinction for application resources
- Unclear parent-child relationships
- All Compute resources appeared in Internal zone or Other categories

### After
- Dedicated "⚙️ Application Tier" subgraph zone
- App Service Plans and Function Apps clearly separated
- Parent-child relationships visualized:
  - app_service_plan subgraph contains child function_apps
  - Proper nesting with indentation
  - Clear hierarchy in diagram structure

## Diagram Zone Order (Updated)
1. Internet-Facing Zone
2. **Application Tier Zone** (NEW)
3. Internal Zone (Compute, Containers, Network)
4. Data Tier Zone
5. PaaS/Identity Zone
6. API Management (if present)
7. Other Resources

## Supported Resource Types

### Azure
- azurerm_app_service_plan → children: function_apps
- azurerm_linux_function_app
- azurerm_windows_function_app
- azurerm_app_service
- azurerm_linux_web_app
- azurerm_windows_web_app
- azurerm_service_plan

### AWS
- aws_elastic_beanstalk_environment (future support)

## Testing Verification
When generating AzureGoat diagram, verify:
1. app_service_plan appears in "⚙️ Application Tier" subgraph
2. function_app and function_app_front are grouped under app_service_plan
3. Hierarchy is visually represented with indentation
4. No app resources incorrectly appear in Internet-Facing or Internal zones
5. Load Balancers remain in Internal/Compute tier
6. Application tier has proper styling and borders

## Code Quality
- Syntax validated: ✓
- No breaking changes to existing functionality
- Backward compatible with existing diagrams
- Follows existing code patterns and conventions
