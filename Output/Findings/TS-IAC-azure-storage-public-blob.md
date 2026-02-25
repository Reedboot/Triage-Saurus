# Finding: TS-IAC-azure-storage-public-blob

Rule: Rules/terraform-container-public-blob.yml
Severity: MEDIUM

Summary:
Terraform defines an azurerm_storage_container with container_access_type set to "blob". This can indicate container-level public blob access allowing anonymous read access to blobs.

Location:
- File: tfscripts/main.tf
- Resource: azurerm_storage_container.pallas

Evidence (redacted):
- container_access_type = "blob"

Recommended Action:
- Review whether this container should be publicly accessible. If not, change container_access_type to "private" or manage access using SAS tokens or Azure RBAC.
- Consider enforcement via CI checks or IaC policy (e.g., Sentinel, OPA) to prevent accidental public blob access.

Notes:
- This finding was auto-generated and linked to rule TS-IAC-azure-storage-public-blob. Human review is required to confirm exposure and impact.
