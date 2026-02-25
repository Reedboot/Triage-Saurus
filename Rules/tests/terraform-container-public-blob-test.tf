resource "azurerm_storage_container" "pallas" {
  name                  = "pallas"
  storage_account_name  = azurerm_storage_account.lab.name
  container_access_type = "blob"
}
