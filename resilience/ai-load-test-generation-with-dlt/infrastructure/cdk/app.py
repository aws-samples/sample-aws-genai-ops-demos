#!/usr/bin/env python3
"""CDK app for the AI Load Test Generator Agent (AgentCore Runtime, VPC-private).

Conforms to sample-aws-genai-ops-demos contributor standards:
- AWS CDK (Python) as the IaC.
- Solution adoption tracking on the MAIN stack description only (here in app.py,
  not in the stack class) — id uksb-do9bhieqqh, tags (demo, pillar).
- Stack id carries a region suffix to avoid global resource conflicts.

Context inputs (pass with `-c key=value`, or via deploy scripts):
  region                 deploy region                 (default: env/CDK)
  bedrockRegion          Bedrock invoke region         (default: region)
  bedrockModelPrimary    primary inference-profile id  (optional)
  bedrockModelFallback   fallback inference-profile id (optional)
  bedrockProfileArns     comma list: profile + FM ARNs (IAM invoke scope)
  dltStackName           DLT stack name (empty = DLT not connected)
  dltRegion              DLT stack region
  dltApiGatewayArn       execute-api ARN scope         (derived in deploy-all.sh)
  dltScenariosBucketArn  DLT scenarios bucket ARN
  dltStackArn            DLT stack ARN (DescribeStacks scope)
  enableXray             "true" to grant X-Ray IAM (needs Transaction Search)
  containerUri           skip the CodeBuild image build, use this image URI (CI/fast synth)
  networkMode            "public" (default) or "vpc". PUBLIC creates no VPC and
                         runs the runtime in AWS-managed egress; VPC places the
                         runtime ENIs in a private VPC (egress control + private
                         AWS-service and private-target reach). Inbound is IAM
                         SigV4 in BOTH modes — PUBLIC does not expose an inbound
                         endpoint.
"""
import os

import aws_cdk as cdk

from lib.agent_stack import AILoadTestGenStack

app = cdk.App()


def ctx(key: str, default: str = "") -> str:
    return app.node.try_get_context(key) or os.environ.get(key.upper(), "") or default


region = ctx("region") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
# Stack id: fixed name + region suffix (repo standard; avoids global resource
# conflicts across regions). Not user-overridable — matches the other demos.
stack_id = f"AILoadTestGen-{region}"

AILoadTestGenStack(
    app,
    stack_id,
    env=cdk.Environment(account=account, region=region),
    # Solution adoption tracking — MAIN stack only, tags (demo-name, pillar).
    description=(
        "AI Load Test Generator Agent on Bedrock AgentCore Runtime "
        "(uksb-do9bhieqqh)(tag:ai-load-test-generation-with-dlt,resilience)"
    ),
    bedrock_region=ctx("bedrockRegion") or region,
    bedrock_model_primary=ctx("bedrockModelPrimary"),
    bedrock_model_fallback=ctx("bedrockModelFallback"),
    bedrock_profile_arns=[a for a in ctx("bedrockProfileArns").split(",") if a],
    dlt_stack_name=ctx("dltStackName"),
    dlt_region=ctx("dltRegion"),
    dlt_api_gateway_arn=ctx("dltApiGatewayArn"),
    dlt_scenarios_bucket_arn=ctx("dltScenariosBucketArn"),
    dlt_stack_arn=ctx("dltStackArn"),
    enable_xray=ctx("enableXray").lower() == "true",
    container_uri=ctx("containerUri"),
    network_mode=ctx("networkMode") or "public",
)

app.synth()
