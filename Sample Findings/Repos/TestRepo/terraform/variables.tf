variable "location" {
  type        = string
  description = "Azure region."
  default     = "eastus"
}

variable "name_prefix" {
  type        = string
  description = "Prefix used for resource names."
  default     = "demo"
}

variable "vpn_public_ips" {
  type        = list(string)
  description = "Public egress IPs for your VPN."
  default = [
    "203.0.113.10",
    "203.0.113.11",
    "198.51.100.25"
  ]
}

variable "app_sku_name" {
  type        = string
  description = "App Service plan SKU."
  default     = "B1"
}

variable "sql_admin_username" {
  type        = string
  description = "SQL admin username (also stored in Key Vault)."
  default     = "sqladminuser"
}

