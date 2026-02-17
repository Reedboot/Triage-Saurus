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

variable "tfstate_container" {
  type        = string
  description = "Blob container name for Terraform state."
  default     = "tfstate"
}

variable "pipeline_principal_object_id" {
  type        = string
  description = "Object ID of the pipeline identity (service connection SP/managed identity) that should be able to access the state container."
}

