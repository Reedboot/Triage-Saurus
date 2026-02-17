output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "web_app_name" {
  value = azurerm_linux_web_app.app.name
}

output "web_app_default_hostname" {
  value = azurerm_linux_web_app.app.default_hostname
}

output "key_vault_name" {
  value = azurerm_key_vault.kv.name
}

output "sql_server_fqdn" {
  value = azurerm_mssql_server.sql.fully_qualified_domain_name
}

