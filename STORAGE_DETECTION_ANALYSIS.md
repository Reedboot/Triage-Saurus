# Storage Detection Rules Analysis

## Current Status

### Rules that WORK (Verified):
1. **context-azure-storage-blob-account-containment** ✅
   - Detects: `resource "azurerm_storage_blob"` with `storage_account_name`
   - Connection: azurerm_storage_account → azurerm_storage_blob
   - Pattern: Handles resource blocks

2. **context-azure-storage-container-account-containment** ✅
   - Detects: `resource "azurerm_storage_container"` with `storage_account_name` OR `storage_account_id`
   - Connection: azurerm_storage_account → azurerm_storage_container
   - Pattern: Handles both account_name and account_id references

3. **context-azure-storage-blob** (Asset Detection) ✅
   - Detects blob resources and modules
   - Captures properties: storage_account_name, storage_container_name, type

4. **context-azure-storage-container** (Asset Detection) ✅
   - Detects container resources and modules
   - Captures properties: storage_account_name, storage_account_id, container_access_type

5. **context-azure-storage-share** (Asset Detection) ✅
   - Detects share resources
   - Captures: storage_account_name, quota

6. **context-azure-storage-queue** (Asset Detection) ✅
   - Detects queue resources
   - Captures: storage_account_name

### GAPS IDENTIFIED:

1. **Missing Connection Detection for Queues** ❌
   - No rule detects azurerm_storage_queue → azurerm_storage_account containment relationship
   - Need: context-azure-storage-queue-account-containment

2. **Missing Connection Detection for Shares** ❌
   - No rule detects azurerm_storage_share → azurerm_storage_account containment relationship
   - Need: context-azure-storage-share-account-containment

3. **Data Block Coverage** ❌
   - Blob connection detection doesn't match `data "azurerm_storage_blob"` blocks
   - Need: Update pattern to support both `resource` and `data` blocks

4. **Alternative Connection Reference Methods** (Minor)
   - storage_container_id might be used in some cases (not currently detected)
   - storage_share_id might be used in some cases (not currently detected)

## Recommendations:

1. Add connection detection for queues (CRITICAL)
2. Add connection detection for shares (CRITICAL)
3. Add support for data blocks in blob detection (IMPORTANT)
4. Verify if storage_queue_id and storage_share_id patterns exist (OPTIONAL)
