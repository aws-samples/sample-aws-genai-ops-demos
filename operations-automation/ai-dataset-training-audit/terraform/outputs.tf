# =============================================================================
# outputs.tf - AI Dataset Training Audit (DART) - Terraform
# =============================================================================
# Same keys as the CDK CfnOutputs (dataset-training-audit-stack.ts), so the
# README's `aws ecs run-task` command reads identical values on either IaC
# path. ecr_repository_url is Terraform-only: the deploy script reads it to tag
# and push the image before the second apply.

output "output_bucket_name" {
  description = "S3 bucket the agent writes preflight reports to (reports/<run_id>.md)."
  value       = aws_s3_bucket.output.id
}

output "audit_table_name" {
  description = "DynamoDB table holding the best-effort audit trail."
  value       = aws_dynamodb_table.audit.name
}

output "cluster_name" {
  description = "ECS cluster the Fargate task runs in."
  value       = aws_ecs_cluster.dart.name
}

output "task_security_group_id" {
  description = "Security group to pass to `aws ecs run-task` (--network-configuration securityGroups=[...])."
  value       = aws_security_group.task.id
}

output "public_subnet_ids" {
  description = "Comma-joined public subnet ids for the run-task network configuration (assignPublicIp=ENABLED)."
  value       = join(",", aws_subnet.public[*].id)
}

output "task_definition_arn" {
  description = "ARN of the Fargate task definition."
  value       = aws_ecs_task_definition.dart.arn
}

# -- Terraform-only (consumed by deploy-all-terraform) ------------------------

output "ecr_repository_url" {
  description = "ECR repository URL the deploy script tags and pushes the container image to before the second terraform apply."
  value       = aws_ecr_repository.dart.repository_url
}
