data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  rg_name  = "${var.name_prefix}-tfstate-rg"
  sa_name  = lower(replace("${var.name_prefix}tfstate${random_string.suffix.result}", "/[^0-9a-z]/", ""))
  tags     = { project = "TestRepo", managedBy = "terraform" }
}

resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "sa" {
  name                     = local.sa_name
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  # Prevent public blobs/containers.
  allow_nested_items_to_be_public = false

  # Prefer OAuth (AAD) over access keys for data-plane access.
  # Note: If your org relies on shared key auth, set this back to true.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  tags = local.tags
}

resource "azurerm_storage_container" "tfstate" {
  name                  = var.tfstate_container
  storage_account_name  = azurerm_storage_account.sa.name
  container_access_type = "private"
}

# Restrict data-plane access to the pipeline identity via RBAC.
resource "azurerm_role_assignment" "pipeline_blob_contributor" {
  scope                = azurerm_storage_account.sa.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.pipeline_principal_object_id
}

