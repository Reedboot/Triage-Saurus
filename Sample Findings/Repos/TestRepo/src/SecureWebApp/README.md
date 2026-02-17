# SecureWebApp (.NET 8)

This is a minimal .NET 8 web app intended to run on Azure App Service.

## Key Vault integration

- Set `KeyVaultUri` (App Setting) to the Key Vault URI.
- The app uses `DefaultAzureCredential`, which works with Azure Managed Identity on App Service.

## SQL configuration

The app reads:

- `Sql:Server`
- `Sql:Database`
- `Sql:Username` (store in Key Vault)
- `Sql:Password` (store in Key Vault)

Endpoints use **parameterized queries** to avoid SQL injection.

