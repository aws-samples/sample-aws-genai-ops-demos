# =============================================================================
# Variables - SaaS Status MCP Server (Terraform)
# =============================================================================
# Copy terraform.tfvars.example to terraform.tfvars and fill in your values.
# The deploy-all-terraform.ps1 script handles this automatically.

# -- Runtime (AgentCore) ------------------------------------------------------

variable "runtime_region" {
  description = "AWS region where the AgentCore Runtime is deployed (e.g. eu-west-3)."
  type        = string
}

variable "account_id" {
  description = "AWS account ID. Auto-detected by deploy-all-terraform.ps1."
  type        = string
}

variable "service_name" {
  description = "Name for the MCP server as it appears in the DevOps Agent console."
  type        = string
  default     = "saas-status-mcp"

  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]+$", var.service_name))
    error_message = "service_name must match [a-zA-Z0-9_-]+."
  }
}

variable "runtime_name" {
  description = "AgentCore Runtime name — must match [a-zA-Z][a-zA-Z0-9_]{0,47} (no hyphens)."
  type        = string
  default     = "saas_status_mcp"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.runtime_name))
    error_message = "runtime_name must match [a-zA-Z][a-zA-Z0-9_]{0,47} — no hyphens allowed by AgentCore."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days for the runtime."
  type        = number
  default     = 14
}

variable "providers_poll_interval" {
  description = "Seconds between S3 conditional-GET polls for the provider registry."
  type        = number
  default     = 60
}

# -- DevOps Agent registration (optional) -------------------------------------
# Leave agent_space_arn empty to skip registration (runtime-only deploy).

variable "agent_space_arn" {
  description = <<-EOT
    Full ARN of the DevOps Agent Space to register the MCP server against.
    Example: arn:aws:aidevops:eu-west-1:123456789012:agentspace/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    Leave empty to skip DevOps Agent registration.
  EOT
  type    = string
  default = ""

  validation {
    condition = (
      var.agent_space_arn == "" ||
      can(regex("^arn:aws[a-z-]*:aidevops:[a-z0-9-]+:[0-9]+:agentspace/.+$", var.agent_space_arn))
    )
    error_message = "agent_space_arn must be empty or a valid Agent Space ARN."
  }
}
