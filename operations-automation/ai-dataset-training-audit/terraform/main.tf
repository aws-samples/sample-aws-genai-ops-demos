# =============================================================================
# main.tf - AI Dataset Training Audit (DART) - Terraform
# =============================================================================
# Terraform mirror of the single CDK stack (../cdk/lib/dataset-training-audit-
# stack.ts). Same resource names, same IAM boundaries, same outputs — the two
# IaC paths cannot be deployed side by side in the same account and region.
#
#   main.tf                     providers, data sources, locals (this file)
#   dataset-training-audit.tf   the one stack-equivalent resource file
#   variables.tf                inputs
#   outputs.tf                  outputs (same keys as the CDK CfnOutputs)
#   tracking.tf                 zero-cost CloudFormation adoption marker stack
#
# The container image is built and pushed by the Terraform deploy script
# (../deploy-all-terraform.{sh,ps1}): the task definition interpolates
# "${aws_ecr_repository.dart.repository_url}:${var.image_tag}", and
# var.image_tag has no default, so a bare `terraform apply` fails fast at plan
# time. A plain apply is unsupported; always use the deploy script.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40.0, < 7.0.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Allow `terraform validate` / offline `terraform plan` (e.g. the marker-stack
  # toggle check) to run without live credentials. These only skip the
  # provider's own pre-flight validation/account lookup; every real API call at
  # `apply` time still requires valid credentials. Set them false if you want
  # the provider to fail fast on bad credentials before apply.
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # ---------------------------------------------------------------------------
  # CRIS prefix (design.md sections 3, 5) — replicates the region->prefix map in
  # shared/utils/aws_utils.py get_bedrock_model_id() and the inline CDK mapping
  # in dataset-training-audit-stack.ts. geo "ap" MUST map to "apac" (not "ap"),
  # and any geo other than us/eu/ap falls back to "global".
  # ---------------------------------------------------------------------------
  geo         = split("-", var.region)[0]
  cris_prefix = lookup({ us = "us", eu = "eu", ap = "apac" }, local.geo, "global")

  # A `global.`-prefixed cross-region inference profile is addressed under the
  # partition's global-profile HOME region, not the arbitrary deploy region.
  # For us/eu/apac prefixes the profile is addressed under the deploy region.
  global_profile_home = {
    aws          = "us-east-1"
    "aws-us-gov" = "us-gov-west-1"
    "aws-cn"     = "cn-north-1"
  }
  profile_region = local.cris_prefix == "global" ? lookup(local.global_profile_home, local.partition, "us-east-1") : var.region

  # ---------------------------------------------------------------------------
  # Approved Bedrock model ids (static). First entry is the canonical demo
  # default and MUST match the CDK approved list exactly.
  # ---------------------------------------------------------------------------
  approved_model_ids = [
    "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-haiku-4-5",
    "amazon.nova-pro-v1:0",
  ]

  # Two ARN families per approved model id, identical to the CDK task role:
  #   foundation-model  wildcard region, NO account id (covers CRIS fan-out)
  #   inference-profile region-specific (home-regioned for global.), account-scoped
  bedrock_fm_arns = [
    for id in local.approved_model_ids :
    "arn:${local.partition}:bedrock:*::foundation-model/${id}"
  ]
  bedrock_profile_arns = [
    for id in local.approved_model_ids :
    "arn:${local.partition}:bedrock:${local.profile_region}:${local.account_id}:inference-profile/${local.cris_prefix}.${id}"
  ]
  bedrock_resources = concat(local.bedrock_fm_arns, local.bedrock_profile_arns)

  tags = {
    Project   = "ai-dataset-training-audit"
    ManagedBy = "terraform"
  }
}
