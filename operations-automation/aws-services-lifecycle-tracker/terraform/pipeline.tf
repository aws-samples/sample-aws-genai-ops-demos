# =============================================================================
# pipeline.tf - Refresh pipeline (CDK: pipeline-stack.ts)
# =============================================================================
# One code bundle, two Lambda functions:
#  - pipeline: Lambda durable function running the whole refresh
#    (extract -> scan -> reconcile -> notify) as one checkpointed execution.
#    Invoked through the `live` alias (durable functions need a qualified ARN).
#  - api: plain function behind the HTTP API (api.tf) serving UI actions,
#    starting/observing pipeline executions, and receiving the weekly schedule.
#
# The code bundle is zipped from var.backend_stage_dir, which the deploy script
# fills with backend/*.py, shared/utils/aws_utils.py and the pip-installed
# Linux/arm64 wheels (no Docker).
# =============================================================================

locals {
  pipeline_function_name = "aws-services-lifecycle-pipeline"
  api_function_name      = "aws-services-lifecycle-api"
  python_runtime         = "python3.14"

  # Hub-and-spoke (issue #144). Fixed names so spoke trust policies can pin the
  # exact principal; must match SPOKE_ROLE_NAME / HUB_PIPELINE_ROLE_NAME in
  # cdk/lib/scan-permissions.ts.
  spoke_role_name        = "LifecycleTrackerScanRole"
  hub_pipeline_role_name = var.pipeline_role_name

  # Read-only calls made by backend/account_discovery.py scanners.
  # Copy of SCANNER_READ_ACTIONS in cdk/lib/scan-permissions.ts: adding a
  # scanner means adding its List/Describe calls in BOTH files.
  scanner_read_actions = [
    "lambda:ListFunctions",
    "rds:DescribeDBInstances", "rds:DescribeDBClusters",
    "eks:ListClusters", "eks:DescribeCluster",
    "elasticache:DescribeCacheClusters",
    "es:ListDomainNames", "es:DescribeDomain",
    "kafka:ListClustersV2",
    "neptune:DescribeDBClusters",
    "glue:GetJobs",
    "elasticbeanstalk:DescribeEnvironments",
    "ec2:DescribeInstances",
    "tag:GetResources",
  ]

  # AWS Health cross-check (#141) + Support tier inference (#144).
  health_read_actions = [
    "health:DescribeEvents", "health:DescribeAffectedEntities",
    "support:DescribeSeverityLevels",
  ]

  table_environment = {
    LIFECYCLE_TABLE_NAME   = aws_dynamodb_table.lifecycle.name
    CONFIG_TABLE_NAME      = aws_dynamodb_table.config.name
    STATE_TABLE_NAME       = aws_dynamodb_table.state.name
    INVENTORY_TABLE_NAME   = aws_dynamodb_table.inventory.name
    ACTION_PLAN_TABLE_NAME = aws_dynamodb_table.action_plan.name
    NOTIFICATION_TOPIC_ARN = aws_sns_topic.notifications.arn
  }

  backend_owned_tables = [
    aws_dynamodb_table.lifecycle, aws_dynamodb_table.state,
    aws_dynamodb_table.inventory, aws_dynamodb_table.action_plan,
  ]
}

# -- Notifications + scheduler DLQ -------------------------------------------

resource "aws_sns_topic" "notifications" {
  name         = "aws-services-lifecycle-notifications"
  display_name = "AWS Services Lifecycle Extraction Notifications"
  tags         = local.tags
}

resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "aws-services-lifecycle-scheduler-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

# enforceSSL: deny any non-TLS access to the queue
resource "aws_sqs_queue_policy" "scheduler_dlq_ssl" {
  queue_url = aws_sqs_queue.scheduler_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sqs:*"
      Resource  = aws_sqs_queue.scheduler_dlq.arn
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# -- Shared code bundle -------------------------------------------------------

data "archive_file" "backend" {
  type        = "zip"
  source_dir  = "${path.module}/${var.backend_stage_dir}"
  output_path = "${path.module}/build/backend.zip"
  excludes    = ["__pycache__", "*.pyc", "*.pyo"]
}

# -- Shared data-plane permissions (issue #116 boundary) ---------------------
# Full access to backend-owned tables, read + UpdateItem only on the repo-owned
# configuration table (no Put/Delete/BatchWrite).

resource "aws_iam_policy" "data_access" {
  name        = "aws-services-lifecycle-data-access"
  description = "Lifecycle tracker Lambda access to DynamoDB, Bedrock, AWS Health and read-only discovery APIs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAgentOwnedAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
          "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
        ]
        Resource = flatten([for t in local.backend_owned_tables : [t.arn, "${t.arn}/index/*"]])
      },
      {
        Sid      = "DynamoDBConfigReadAndUpdate"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"]
        Resource = [aws_dynamodb_table.config.arn, "${aws_dynamodb_table.config.arn}/index/*"]
      },
      {
        Sid    = "BedrockModelInvocation"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"]
        Resource = [
          "arn:${local.partition}:bedrock:*::foundation-model/*",
          "arn:${local.partition}:bedrock:*:${local.account_id}:inference-profile/*",
        ]
      },
      {
        # Scan-time cross-check against open planned lifecycle notices (#141).
        # Needs Business/Enterprise Support at runtime. No resource-level permissions.
        Sid      = "HealthAPIAccess"
        Effect   = "Allow"
        Action   = local.health_read_actions
        Resource = "*"
      },
      {
        # Extended Support cost exposure (#142): Price List API + vCPUs per class.
        Sid      = "ExtendedSupportPricing"
        Effect   = "Allow"
        Action   = ["pricing:GetProducts", "ec2:DescribeInstanceTypes"]
        Resource = "*"
      },
      {
        # List/Describe calls: read-only, no resource scoping available
        Sid      = "AccountResourceDiscovery"
        Effect   = "Allow"
        Action   = local.scanner_read_actions
        Resource = "*"
      },
      {
        # Multi-account scan (#144): assume the read-only spoke role in member accounts
        Sid      = "HubAndSpokeScan"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = "arn:${local.partition}:iam::*:role/${local.spoke_role_name}"
      },
      {
        Sid    = "OrganizationsRead"
        Effect = "Allow"
        Action = [
          "organizations:DescribeOrganization", "organizations:ListAccounts", "organizations:DescribeAccount",
          "organizations:ListRoots", "organizations:ListOrganizationalUnitsForParent", "organizations:ListParents",
          "organizations:DescribeOrganizationalUnit", "organizations:ListAccountsForParent",
        ]
        Resource = "*"
      },
      {
        Sid      = "PublishNotifications"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.notifications.arn
      },
    ]
  })

  tags = local.tags
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# -- Durable pipeline function ------------------------------------------------

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/${local.pipeline_function_name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

# Fixed role name so spoke trust policies can pin the exact principal.
resource "aws_iam_role" "pipeline" {
  name               = local.hub_pipeline_role_name
  description        = "Lifecycle tracker pipeline role (hub): scans this account and assumes spoke roles"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "pipeline_data_access" {
  role       = aws_iam_role.pipeline.name
  policy_arn = aws_iam_policy.data_access.arn
}

resource "aws_iam_role_policy_attachment" "pipeline_basic" {
  role       = aws_iam_role.pipeline.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Checkpoint permissions (lambda:CheckpointDurableExecution, GetDurableExecutionState)
resource "aws_iam_role_policy_attachment" "pipeline_durable" {
  role       = aws_iam_role.pipeline.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicDurableExecutionRolePolicy"
}

resource "aws_lambda_function" "pipeline" {
  function_name    = local.pipeline_function_name
  description      = "Lifecycle refresh pipeline: extract -> scan -> reconcile -> notify (Lambda durable function)"
  role             = aws_iam_role.pipeline.arn
  runtime          = local.python_runtime
  architectures    = ["arm64"]
  handler          = "pipeline.handler"
  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256
  memory_size      = 1024
  timeout          = 900
  publish          = true # durable functions are invoked through a qualified ARN (alias below)

  # Durable execution can only be enabled at create time.
  durable_config {
    execution_timeout = 7200 # 2 hours for the whole checkpointed run
    retention_period  = 14   # days of execution history
  }

  # Durable functions only support JSON-format logs (platform.start/report events).
  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "INFO"
    log_group             = aws_cloudwatch_log_group.pipeline.name
  }

  environment {
    variables = merge(
      local.table_environment,
      { SPOKE_ROLE_NAME = local.spoke_role_name },
      var.spoke_external_id != "" ? { SPOKE_EXTERNAL_ID = var.spoke_external_id } : {},
    )
  }

  tags = local.tags

  depends_on = [
    aws_iam_role_policy_attachment.pipeline_data_access,
    aws_iam_role_policy_attachment.pipeline_basic,
    aws_iam_role_policy_attachment.pipeline_durable,
  ]
}

resource "aws_lambda_alias" "pipeline_live" {
  name             = "live"
  description      = "Qualified ARN used to start durable executions"
  function_name    = aws_lambda_function.pipeline.function_name
  function_version = aws_lambda_function.pipeline.version
}

# -- Plain API function (UI actions, pipeline control, weekly schedule) ------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.api_function_name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "api" {
  name               = "aws-services-lifecycle-api-role"
  description        = "Lifecycle tracker API function role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "api_data_access" {
  role       = aws_iam_role.api.name
  policy_arn = aws_iam_policy.data_access.arn
}

resource "aws_iam_role_policy_attachment" "api_basic" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "api_pipeline_control" {
  name = "PipelineControl"
  role = aws_iam_role.api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "PipelineControl"
      Effect = "Allow"
      Action = [
        "lambda:InvokeFunction",
        "lambda:GetDurableExecution",
        "lambda:GetDurableExecutionHistory",
        "lambda:ListDurableExecutionsByFunction",
      ]
      Resource = [aws_lambda_function.pipeline.arn, "${aws_lambda_function.pipeline.arn}:*"]
    }]
  })
}

resource "aws_lambda_function" "api" {
  function_name    = local.api_function_name
  description      = "Lifecycle tracker API: UI actions, refresh pipeline control, weekly schedule entry point"
  role             = aws_iam_role.api.arn
  runtime          = local.python_runtime
  architectures    = ["arm64"]
  handler          = "api.handler"
  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256
  memory_size      = 512
  timeout          = 300

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api.name
  }

  environment {
    variables = merge(
      local.table_environment,
      { PIPELINE_FUNCTION_ARN = aws_lambda_alias.pipeline_live.arn },
    )
  }

  tags = local.tags

  depends_on = [
    aws_iam_role_policy_attachment.api_data_access,
    aws_iam_role_policy_attachment.api_basic,
    aws_iam_role_policy.api_pipeline_control,
  ]
}

# -- Schedules (EventBridge Scheduler -> API function) -----------------------
# The weekly run goes through the API function so it reuses the same
# naming / adopt-running logic as a UI-triggered refresh.

resource "aws_iam_role" "scheduler" {
  name        = "aws-services-lifecycle-scheduler-role"
  description = "EventBridge Scheduler role invoking the lifecycle tracker API function"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "InvokeApiFunction"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.api.arn, "${aws_lambda_function.api.arn}:*"]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.scheduler_dlq.arn
      },
    ]
  })
}

resource "aws_scheduler_schedule" "weekly_refresh" {
  name                         = "aws-services-lifecycle-weekly-refresh"
  description                  = "Weekly end-to-end lifecycle refresh (durable pipeline)"
  schedule_expression          = "rate(7 days)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.api.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "start_refresh", refresh_origin = "Auto" })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }

    dead_letter_config {
      arn = aws_sqs_queue.scheduler_dlq.arn
    }
  }

  depends_on = [aws_iam_role_policy.scheduler]
}
