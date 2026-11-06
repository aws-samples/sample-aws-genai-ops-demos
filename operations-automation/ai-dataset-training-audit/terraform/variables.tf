# =============================================================================
# variables.tf - AI Dataset Training Audit (DART) - Terraform
# =============================================================================
# deploy-all-terraform.{sh,ps1} supplies region + image_tag; copy
# terraform.tfvars.example to terraform.tfvars for a manual run (but note: a
# bare `terraform apply` is unsupported because the image build/push is owned
# by the deploy script — see main.tf).

variable "region" {
  description = "AWS region to deploy into (Amazon Bedrock and Amazon Comprehend must be available there)."
  type        = string
}

variable "enable_deployment_metrics" {
  description = "Create a zero-cost CloudFormation marker stack so this deployment is counted in AWS solution adoption metrics (see tracking.tf). Set to false to opt out."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Tag of the container image the deploy script built and pushed to the demo's ECR repository. No default: a bare `terraform apply` must fail at plan time because the image build/push is owned by deploy-all-terraform."
  type        = string
}

variable "model_id" {
  description = "Base Bedrock model id (no CRIS prefix) injected into the container as MODEL_ID. Defaults to the canonical demo model; must be present in the approved list in main.tf or Bedrock invoke is denied."
  type        = string
  default     = "anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "allowed_s3_buckets" {
  description = "Comma-separated S3 bucket allowlist for dataset input path validation (empty = allow any, documented). Match this to input_buckets when granting scoped read access."
  type        = string
  default     = ""
}

variable "allowed_s3_prefixes" {
  description = "Comma-separated S3 key-prefix allowlist for dataset input path validation (empty = allow any)."
  type        = string
  default     = ""
}

variable "allowed_local_base_dir" {
  description = "Comma-separated local base directory allowlist for dataset input path validation (empty = allow any)."
  type        = string
  default     = ""
}

variable "log_level" {
  description = "Container log level."
  type        = string
  default     = "INFO"
}

variable "input_buckets" {
  description = "Opt-in list of customer/source S3 buckets the task role may READ (s3:GetObject + s3:ListBucket), scoped to each named bucket — never \"*\". Empty (default) keeps the default-deny posture: the task reads only its own output bucket. Set ALLOWED_S3_BUCKETS (allowed_s3_buckets) to the same list so path validation and the IAM grant agree."
  type        = list(string)
  default     = []
}
