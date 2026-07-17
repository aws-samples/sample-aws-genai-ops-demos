# =============================================================================
# outputs.tf - SaaS Status MCP Server (Terraform)
# =============================================================================

# -- Runtime ------------------------------------------------------------------

output "runtime_arn" {
  description = "AgentCore Runtime ARN."
  value       = awscc_bedrockagentcore_runtime.mcp.agent_runtime_arn
}

output "runtime_endpoint" {
  description = "MCP invocation endpoint (percent-encoded ARN, used by DevOps Agent and local proxy)."
  value       = local.runtime_endpoint
}

output "runtime_role_arn" {
  description = "IAM role assumed by the AgentCore Runtime."
  value       = aws_iam_role.runtime.arn
}

output "s3_bucket" {
  description = "S3 bucket holding the deployment zip and provider registry."
  value       = data.aws_s3_bucket.runtime.id
}

output "log_group" {
  description = "CloudWatch log group for runtime logs."
  value       = aws_cloudwatch_log_group.runtime.name
}

# -- Registration (only populated when agent_space_arn is set) ----------------

output "service_id" {
  description = "DevOps Agent Service ID (null if registration was skipped)."
  value       = local.register ? awscc_devopsagent_service.mcp[0].service_id : null
}

output "signing_role_arn" {
  description = "SigV4 signing role ARN (null if registration was skipped)."
  value       = local.register ? aws_iam_role.signing[0].arn : null
}
