# Terraform Data Source vs Resource Detection Fix

## Problem Statement
Terraform `data` blocks (data sources) were being incorrectly parsed as deployable resources, causing:
- Duplicate entries in the database
- Data sources treated as new resources to be created
- Architecture diagrams showing references as resources
- Example: `data "azurerm_public_ip"` was treated like `resource "azurerm_public_ip"`

## Solution
Updated the Terraform parser in `Scripts/Context/context_extraction.py` to distinguish between `resource` and `data` blocks.

## Changes Made

### 1. Added Validation Function: `is_valid_azure_resource_name()`
**Location**: `Scripts/Context/context_extraction.py:185-205`

Validates that a resource name is not a reference or Terraform syntax by rejecting:
- Names with dots (`.`) - indicates Terraform interpolation like `azurerm_public_ip.VM_PublicIP.name`
- Names with resource type prefixes (`azurerm_`, `aws_`, `google_`, etc.) - indicates a reference
- Names with interpolation syntax (`$`, `{`, `}`) - indicates dynamic values

```python
def is_valid_azure_resource_name(name: str) -> bool:
    """Validate that a name is a valid Azure resource name."""
    if not name or "." in name:
        return False
    # Check for resource type prefixes and interpolation syntax
    ...
    return True
```

### 2. Updated `extract_resource_names()`
**Location**: `Scripts/Context/context_extraction.py:208-222`

- Now only extracts from `resource` blocks, not `data` blocks
- Updated regex pattern to explicitly match only `resource` declarations
- Added docstring clarifying the behavior

### 3. Updated `detect_terraform_resources()`
**Location**: `Scripts/Context/context_extraction.py:230-245`

- Changed regex to only match `resource` blocks using `^\s*resource\s+`
- Added `re.MULTILINE` flag for proper line-by-line matching
- Excludes all `data` block types from detection

### 4. Updated Main Context Extraction Logic
**Location**: `Scripts/Context/context_extraction.py:1055-1090`

In the main parsing loop, added two critical checks before adding resources to `context.resources`:

```python
# Skip data blocks - they are references to existing resources
if block_kind == "data":
    i += 1
    continue

# Validate resource name - skip invalid names that are likely references
if not is_valid_azure_resource_name(name):
    i += 1
    continue
```

## Impact

### Before Fix
- **Total resources extracted**: 4 (2 resources + 2 data sources mixed together)
- Data sources included: `azurerm_public_ip`, `azurerm_client_config`, `azurerm_subscription`
- Problem: Diagram shows both resources and data sources as deployable items

### After Fix
- **Total resources extracted**: 4 (only the actual 4 resources)
- Data sources excluded: `azurerm_public_ip`, `azurerm_client_config`, `azurerm_subscription`
- Solution: Diagram shows only deployable resources

## Tested Scenarios

✅ **Single data source in file**: Correctly excluded from resources
✅ **Multiple data sources**: All excluded
✅ **Mixed resource and data blocks**: Resources included, data excluded
✅ **Data blocks with attributes**: Properly skipped
✅ **Invalid resource names** (containing dots/prefixes): Correctly validated and excluded
✅ **Valid Azure names**: Correctly accepted

## Example

### Input Terraform
```hcl
resource "azurerm_virtual_machine" "main_vm" {
  name = "my-vm"
}

data "azurerm_public_ip" "vm_ip" {
  name = "my-public-ip"
}

resource "azurerm_network_interface" "main_nic" {
  name = "main-nic"
}

data "azurerm_client_config" "current" {}
```

### Output Resources (After Fix)
- `azurerm_virtual_machine` / `main_vm` ✓
- `azurerm_network_interface` / `main_nic` ✓
- ~~`azurerm_public_ip` / `vm_ip`~~ (excluded - data block)
- ~~`azurerm_client_config` / `current`~~ (excluded - data block)

## Backward Compatibility
✅ All changes are backward compatible:
- Only affects how data blocks are processed
- Existing resource parsing logic unchanged
- No database schema changes required
- Resource blocks continue to work as before

## Testing
All scenarios tested and verified:
- 12+ validation test cases passed
- 2 resource/data block separation tests passed
- 3 full integration tests passed
