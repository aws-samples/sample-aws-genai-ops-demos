# =============================================================================
# tracking.tf - Solution adoption tracking for the Terraform path
# =============================================================================
# The AWS Solution Adoption Dashboard counts deployments by reading the
# Description of CloudFormation stacks; Terraform resources are invisible to it.
# So the Terraform path creates one zero-cost CloudFormation stack whose only
# job is to carry the same tracking code and tags as the CDK Pipeline stack
# (cdk/bin/app.ts). The marker resource, a WaitConditionHandle, does nothing
# and costs nothing. Set enable_deployment_metrics = false to opt out.
#
# Exactly one tracked stack per deployment either way: the CDK and Terraform
# paths never coexist in the same account and region.
# =============================================================================

resource "aws_cloudformation_stack" "tracking" {
  count = var.enable_deployment_metrics ? 1 : 0

  name = "AWSServicesLifecycleTrackerTracking-${var.region}"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "AWS Services Lifecycle Tracker, Terraform deployment marker (uksb-do9bhieqqh)(tag:lifecycle-tracker,operations-automation)"
    Resources = {
      Marker = { Type = "AWS::CloudFormation::WaitConditionHandle" }
    }
  })

  tags = local.tags
}
