output "tfstate_resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "tfstate_storage_account_name" {
  value = azurerm_storage_account.sa.name
}

output "tfstate_container_name" {
  value = azurerm_storage_container.tfstate.name
}

