# =============================================================================
# registration.tf - SaaS Status MCP Server: DevOps Agent Registration
# =============================================================================
# Registers the deployed AgentCore Runtime as an MCP tool source on a DevOps
# Agent Space. This file is a no-op when agent_space_arn is empty.
#
# Resources are deployed to the Agent Space's region, which is parsed from
# the agent_space_arn variable and may differ from the runtime region.
# =============================================================================

locals {
  register = var.agent_space_arn != ""

  space_region = local.register ? regex(
    "^arn:aws[a-z-]*:aidevops:([^:]+):[0-9]+:agentspace/.+$",
    var.agent_space_arn
  )[0] : ""

  space_id = local.register ? regex(
    "^arn:aws[a-z-]*:aidevops:[^:]+:[0-9]+:agentspace/(.+)$",
    var.agent_space_arn
  )[0] : ""
}

provider "aws" {
  alias  = "space"
  region = local.space_region != "" ? local.space_region : var.runtime_region
}

provider "awscc" {
  alias  = "space"
  region = local.space_region != "" ? local.space_region : var.runtime_region
}

resource "aws_iam_role" "signing" {
  count = local.register ? 1 : 0

  provider    = aws.space
  name        = "SaasStatusMcpSigningRole-${local.space_region}"
  description = "SigV4 signing role - DevOps Agent assumes this to invoke the SaaS Status MCP runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "aidevops.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:aidevops:${local.space_region}:${var.account_id}:service/*"
        }
      }
    }]
  })

  tags = {
    Project   = "saas-status-mcp"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "signing" {
  count = local.register ? 1 : 0

  provider = aws.space
  name     = "InvokeSaasStatusMcpRuntime"
  role     = aws_iam_role.signing[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "bedrock-agentcore:InvokeAgentRuntime"
      Resource = [
        awscc_bedrockagentcore_runtime.mcp.agent_runtime_arn,
        "${awscc_bedrockagentcore_runtime.mcp.agent_runtime_arn}/*",
      ]
    }]
  })
}

# IAM propagation delay — DevOps Agent service validates the signing role at
# create time, so we must wait for global IAM consistency first.
resource "time_sleep" "signing_iam_propagation" {
  count           = local.register ? 1 : 0
  create_duration = "15s"
  depends_on      = [aws_iam_role_policy.signing]
}

resource "awscc_devopsagent_service" "mcp" {
  count = local.register ? 1 : 0

  provider     = awscc.space
  service_type = "mcpserversigv4"

  service_details = {
    mcp_server_sig_v4 = {
      name        = var.service_name
      endpoint    = local.runtime_endpoint
      description = "SaaS status pages (Statuspage.io) for upstream dependency checks"
      authorization_config = {
        region   = var.runtime_region
        service  = "bedrock-agentcore"
        role_arn = aws_iam_role.signing[0].arn
      }
    }
  }

  depends_on = [time_sleep.signing_iam_propagation]
}

resource "awscc_devopsagent_association" "mcp" {
  count = local.register ? 1 : 0

  provider       = awscc.space
  agent_space_id = local.space_id
  service_id     = awscc_devopsagent_service.mcp[0].service_id

  configuration = {
    mcp_server_sig_v4 = {
      tools = [
        "list_providers",
        "get_service_status",
        "get_active_events",
        "check_all_dependencies",
      ]
    }
  }
}
