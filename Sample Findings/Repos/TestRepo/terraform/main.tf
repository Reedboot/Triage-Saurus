data "azurerm_client_config" "current" {}

locals {
  rg_name            = "${var.name_prefix}-rg"
  # Key Vault name: 3-24 chars, alphanumeric/hyphen, must be globally unique.
  kv_name            = substr(lower(replace("${var.name_prefix}kv${substr(data.azurerm_client_config.current.subscription_id, 0, 8)}", "/[^0-9a-z]/", "")), 0, 24)
  ai_name            = "${var.name_prefix}-ai"
  plan_name          = "${var.name_prefix}-plan"
  webapp_name        = "${var.name_prefix}-web-${substr(data.azurerm_client_config.current.subscription_id, 0, 6)}"
  sql_server_name    = "${var.name_prefix}-sql-${substr(data.azurerm_client_config.current.subscription_id, 0, 6)}"
  sql_database_name  = "${var.name_prefix}-db"
  tags               = { project = "TestRepo", managedBy = "terraform" }
}

resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_application_insights" "ai" {
  name                = local.ai_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  tags                = local.tags
}

resource "azurerm_service_plan" "plan" {
  name                = local.plan_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  os_type             = "Linux"
  sku_name            = var.app_sku_name
  tags                = local.tags
}

resource "azurerm_key_vault" "kv" {
  name                       = local.kv_name
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 7
  tags                       = local.tags

  public_network_access_enabled = true

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = var.vpn_public_ips
  }
}

resource "random_password" "sql_admin_password" {
  length           = 32
  special          = true
  override_special = "_%@"
}

resource "azurerm_mssql_server" "sql" {
  name                         = local.sql_server_name
  location                     = azurerm_resource_group.rg.location
  resource_group_name          = azurerm_resource_group.rg.name
  version                      = "12.0"
  administrator_login          = var.sql_admin_username
  administrator_login_password = random_password.sql_admin_password.result
  minimum_tls_version          = "1.2"
  tags                         = local.tags
}

resource "azurerm_mssql_database" "db" {
  name           = local.sql_database_name
  server_id      = azurerm_mssql_server.sql.id
  sku_name       = "Basic"
  max_size_gb    = 2
  zone_redundant = false
  tags           = local.tags
}

resource "azurerm_mssql_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_mssql_firewall_rule" "vpn" {
  for_each         = toset(var.vpn_public_ips)
  name             = "VPN-${replace(each.key, ".", "-")}"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = each.key
  end_ip_address   = each.key
}

resource "azurerm_key_vault_secret" "sql_username" {
  name         = "sql-admin-username"
  value        = var.sql_admin_username
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "sql_password" {
  name         = "sql-admin-password"
  value        = random_password.sql_admin_password.result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_linux_web_app" "app" {
  name                = local.webapp_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  service_plan_id     = azurerm_service_plan.plan.id
  https_only          = true
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on = true

    application_stack {
      dotnet_version = "8.0"
    }
  }

  app_settings = {
    "KeyVaultUri" = azurerm_key_vault.kv.vault_uri

    "Sql__Server"   = "tcp:${azurerm_mssql_server.sql.fully_qualified_domain_name},1433"
    "Sql__Database" = azurerm_mssql_database.db.name

    # Use Key Vault references for credentials.
    "Sql__Username" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.sql_username.id})"
    "Sql__Password" = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.sql_password.id})"

    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.ai.connection_string
  }
}

resource "azurerm_role_assignment" "webapp_kv_secrets_user" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_web_app.app.identity[0].principal_id
}
