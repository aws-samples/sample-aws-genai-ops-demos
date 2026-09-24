# =============================================================================
# main.tf - AWS Services Lifecycle Tracker (Terraform)
# =============================================================================
# Terraform equivalent of the CDK stacks in ../cdk (single-account deployment):
#   data.tf      DynamoDB tables + service config populator      (Data stack)
#   auth.tf      Cognito user pool + web client                   (Auth stack)
#   pipeline.tf  Durable pipeline function, API function, IAM,
#                SNS, SQS DLQ, EventBridge Scheduler              (Pipeline stack)
#   api.tf       HTTP API + Cognito JWT authorizer                (Api stack)
#   frontend.tf  S3 + CloudFront for the React SPA                (Frontend stack)
#
# Multi-account (hub-and-spoke) rollout is CDK-only: it relies on a
# CloudFormation StackSet whose template is the synthesized Spoke stack.
#
# The deploy script (../deploy-all-terraform.{ps1,sh}) stages the Lambda
# sources (backend/*.py + pip wheels for arm64 + shared aws_utils.py) into
# .backend-stage/ BEFORE terraform apply, then builds and uploads the frontend
# AFTER apply, once the API URL and Cognito IDs exist.
#
# Same resource names as the CDK path (tables, functions, roles): the two IaC
# paths cannot be deployed side by side in the same account and region.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.25.0, < 7.0.0" # 6.25 adds durable_config on aws_lambda_function
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  tags = {
    Project   = "aws-services-lifecycle-tracker"
    ManagedBy = "terraform"
  }
}
