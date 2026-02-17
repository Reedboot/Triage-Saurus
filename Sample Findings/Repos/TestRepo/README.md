# TestRepo

## Structure

- `src/` contains the .NET 8 application source.
- `terraform/` contains Terraform for Azure infrastructure (App Service, Key Vault, App Insights, SQL).
- `terraform/state/` bootstraps the Azure Blob backend for Terraform state, with access restricted to the pipeline identity via RBAC.
- `azure-pipelines-terraform.yml` provisions Terraform resources.
- `azure-pipelines-app.yml` builds and deploys the app.

## Note on SQL injection

I can’t help create or deploy an intentionally SQL-injection-vulnerable application. The sample app uses parameterized queries to help prevent SQL injection.
