# Knowledge: azurerm_storage_container.pallas (public blob)

Related finding: Output/Findings/TS-IAC-azure-storage-public-blob.md
Related rule: Rules/terraform-container-public-blob.yml (TS-IAC-azure-storage-public-blob)

Summary:
Terraform defines an azurerm_storage_container resource `pallas` with container_access_type = "blob", which may indicate container-level public blob access allowing anonymous read access to blobs. This entry records the redacted evidence and links to the finding and rule for tracking.

Details:
- File: `tfscripts/main.tf`
- Resource: `azurerm_storage_container.pallas`
- Evidence (redacted): `container_access_type = "blob"`
- Inferred service: Azure Storage Container (public blob access)

Action links:
- Finding: Output/Findings/TS-IAC-azure-storage-public-blob.md
- Rule: Rules/terraform-container-public-blob.yml

Notes:
- Human review required to confirm whether the container is intentionally public and to assess impact.
