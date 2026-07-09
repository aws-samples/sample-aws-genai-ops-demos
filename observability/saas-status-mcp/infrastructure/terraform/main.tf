# =============================================================================
# main.tf - SaaS Status MCP Server: AgentCore Runtime Stack
# =============================================================================
# Deploys the AgentCore Runtime and its supporting resources in the runtime
# region.
#
# NOTE: The S3 bucket is created by the deploy script BEFORE terraform apply
# (so the zip upload succeeds first). Terraform reads it via a data source
# rather than managing it - this avoids a double-create conflict and keeps
# the bucket alive across terraform destroy cycles.
#
# Deployed to: var.runtime_region
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.91"
    }
    # Used for IAM propagation delay before AgentCore runtime creation
    time = {
      source  = "hashicorp/time"
      version = "~> 0.9"
    }
  }
}

# -- Providers ----------------------------------------------------------------

provider "aws" {
  region = var.runtime_region
}

provider "awscc" {
  region = var.runtime_region
}

# -- Locals -------------------------------------------------------------------

locals {
  bucket_name = "saas-status-mcp-${var.account_id}-${var.runtime_region}"

  runtime_endpoint = "https://bedrock-agentcore.${var.runtime_region}.amazonaws.com/runtimes/${replace(replace(awscc_bedrockagentcore_runtime.mcp.agent_runtime_arn, ":", "%3A"), "/", "%2F")}/invocations?qualifier=DEFAULT"
}

# -- S3 bucket (read-only reference) -----------------------------------------
# Created by the deploy script before terraform apply. Terraform reads it
# here rather than owning it to avoid double-create conflicts.

data "aws_s3_bucket" "runtime" {
  bucket = local.bucket_name
}

# -- CloudWatch log group -----------------------------------------------------

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/aws/bedrock-agentcore/${var.runtime_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Project   = "saas-status-mcp"
    ManagedBy = "terraform"
  }
}

# -- IAM role for the AgentCore Runtime ---------------------------------------

resource "aws_iam_role" "runtime" {
  name        = "SaasStatusMcpRuntimeRole-${var.runtime_region}"
  description = "Execution role for the SaaS Status MCP AgentCore Runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Project   = "saas-status-mcp"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "runtime" {
  name = "SaasStatusMcpRuntimePolicy"
  role = aws_iam_role.runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadDeploymentZip"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${data.aws_s3_bucket.runtime.arn}/agent/*",
          "${data.aws_s3_bucket.runtime.arn}/config/*",
        ]
      },
      {
        Sid    = "WriteLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.runtime.arn}:*"
      }
    ]
  })
}

# IAM propagation delay — AgentCore validates the role at create time,
# so we must wait for global IAM consistency before creating the runtime.
resource "time_sleep" "iam_propagation" {
  create_duration = "20s"
  depends_on      = [aws_iam_role_policy.runtime]
}

# -- AgentCore Runtime --------------------------------------------------------

resource "awscc_bedrockagentcore_runtime" "mcp" {
  agent_runtime_name     = var.runtime_name
  description            = "SaaS status MCP server for AWS DevOps Agent"
  role_arn               = aws_iam_role.runtime.arn
  protocol_configuration = "MCP"

  agent_runtime_artifact = {
    code_configuration = {
      runtime     = "PYTHON_3_13"
      entry_point = ["main.py"]
      code = {
        s3 = {
          bucket = data.aws_s3_bucket.runtime.id
          prefix = "agent/deployment_package.zip"
        }
      }
    }
  }

  network_configuration = {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    PROVIDERS_BUCKET        = data.aws_s3_bucket.runtime.id
    PROVIDERS_KEY           = "config/providers.json"
    PROVIDERS_POLL_INTERVAL = tostring(var.providers_poll_interval)
  }

  tags = {
    Project   = "saas-status-mcp"
    ManagedBy = "terraform"
  }

  depends_on = [time_sleep.iam_propagation]
}
