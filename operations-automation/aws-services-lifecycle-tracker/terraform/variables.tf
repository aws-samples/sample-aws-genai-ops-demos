# =============================================================================
# variables.tf - AWS Services Lifecycle Tracker (Terraform)
# =============================================================================
# deploy-all-terraform.{ps1,sh} writes terraform.tfvars from your AWS CLI
# configuration; copy terraform.tfvars.example for a manual run.

variable "region" {
  description = "AWS region to deploy into (Lambda durable functions and Amazon Nova must be available there)."
  type        = string
}

variable "backend_stage_dir" {
  description = "Directory holding the staged Lambda code (backend/*.py, shared aws_utils.py and the pip-installed arm64 wheels). Produced by the deploy script."
  type        = string
  default     = ".backend-stage"
}

variable "service_configs_path" {
  description = "Path to service_configs.json (the service definitions seeded into the config table)."
  type        = string
  default     = "../scripts/service_configs.json"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the Lambda functions."
  type        = number
  default     = 30
}

variable "enable_deployment_metrics" {
  description = "Create a zero-cost CloudFormation marker stack so this deployment is counted in AWS solution adoption metrics (see tracking.tf). Set to false to opt out."
  type        = bool
  default     = true
}

variable "pipeline_role_name" {
  description = "Name of the hub pipeline IAM role. Fixed by design (spoke trust policies pin it); IAM is global, so change it only if the name is already taken in this account, e.g. by a CDK deployment of this demo in another region."
  type        = string
  default     = "aws-services-lifecycle-pipeline-role"
}

variable "spoke_external_id" {
  description = "Optional sts:ExternalId presented when the pipeline assumes spoke roles (multi-account, issue #144). Leave empty for single-account."
  type        = string
  default     = ""
}
