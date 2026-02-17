# Terraform state bootstrap

This creates an Azure Storage Account + private container for Terraform state and grants access **only** to the pipeline identity via RBAC.

Inputs:

- `pipeline_principal_object_id`: object ID of the identity used by your Azure DevOps service connection.

Notes:

- This uses OAuth (Azure AD) for blob access (`default_to_oauth_authentication = true`) and disables shared key auth (`shared_access_key_enabled = false`).
- For network-level isolation (private endpoint), you’ll need a VNet + self-hosted agent inside that network.

