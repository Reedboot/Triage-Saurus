# Test file for terraform-connection-string-detection rule
resource "azurerm_storage_account" "storage_labpallas" {
  name                     = "labpallas"
  resource_group_name      = azurerm_resource_group.rg.name
}

output "storage_conn" {
  value = azurerm_storage_account.storage_labpallas.primary_connection_string
}

variable "connection_string" {
  default = "DefaultEndpointsProtocol=https;AccountName=labpallas;AccountKey=REDACTED;EndpointSuffix=core.windows.net"
}
