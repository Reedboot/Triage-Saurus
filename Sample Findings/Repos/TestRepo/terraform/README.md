# Terraform (Azure)

Creates:

- Resource group
- App Service plan + Linux Web App (.NET 8) with System Assigned Managed Identity
- Application Insights
- Key Vault (public access + IP restricted to VPN IPs; `bypass = AzureServices`)
- Azure SQL Server + Database
- Key Vault secrets for SQL admin username/password

Notes:

- Terraform state:
  - `terraform/state/` bootstraps an Azure Storage Account + private container for remote state and grants blob access only to the pipeline identity via RBAC.
  - The pipeline uses Azure AD auth for the backend (`use_azuread_auth=true`).
- `azurerm_key_vault_secret` values and the generated SQL password will exist in Terraform state. For production, prefer injecting secrets out-of-band (pipeline, bootstrap script) and restricting access to the state backend.
