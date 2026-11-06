# =============================================================================
# outputs.tf - AWS Services Lifecycle Tracker (Terraform)
# =============================================================================
# Same values as the CDK stack outputs the deploy script reads with
# `aws cloudformation describe-stacks`; deploy-all-terraform reads these with
# `terraform output -raw`.

# -- Frontend -----------------------------------------------------------------

output "website_url" {
  description = "CloudFront distribution URL of the admin UI."
  value       = "https://${aws_cloudfront_distribution.website.domain_name}"
}

output "website_bucket" {
  description = "S3 bucket holding the built SPA (synced by the deploy script)."
  value       = aws_s3_bucket.website.id
}

output "distribution_id" {
  description = "CloudFront distribution ID (invalidated after each frontend upload)."
  value       = aws_cloudfront_distribution.website.id
}

# -- API + auth (baked into the frontend build as VITE_* variables) ----------

output "api_url" {
  description = "HTTP API base URL used by the frontend (VITE_API_URL, no trailing slash)."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "user_pool_id" {
  description = "Cognito User Pool ID."
  value       = aws_cognito_user_pool.admin.id
}

output "user_pool_client_id" {
  description = "Cognito User Pool Client ID."
  value       = aws_cognito_user_pool_client.web.id
}

# -- Pipeline -----------------------------------------------------------------

output "pipeline_function_alias_arn" {
  description = "Qualified ARN of the durable refresh pipeline (invoke with --durable-execution-name)."
  value       = aws_lambda_alias.pipeline_live.arn
}

output "api_function_arn" {
  description = "ARN of the API function (UI actions, pipeline control, weekly schedule)."
  value       = aws_lambda_function.api.arn
}

output "notification_topic_arn" {
  description = "SNS topic receiving refresh completion summaries."
  value       = aws_sns_topic.notifications.arn
}

output "weekly_schedule_name" {
  description = "EventBridge Scheduler schedule running the weekly refresh."
  value       = aws_scheduler_schedule.weekly_refresh.name
}

output "dead_letter_queue_url" {
  description = "SQS dead-letter queue of the scheduler."
  value       = aws_sqs_queue.scheduler_dlq.url
}

output "region" {
  description = "Deployment region."
  value       = var.region
}
