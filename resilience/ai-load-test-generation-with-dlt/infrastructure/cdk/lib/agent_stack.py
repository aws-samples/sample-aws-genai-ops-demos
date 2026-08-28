"""AI Load Test Generator Agent — AgentCore Runtime (CDK port).

Mirrors infrastructure/cloudformation/template.yaml (CFN) but:
- builds the ARM64 image via DockerImageAsset (no CodeBuild/ECR/source-zip/Lambda),
- creates the VPC/NAT/endpoints via the L2 ec2.Vpc (far fewer lines),
- keeps DLT OPTIONAL (no DLT env/IAM unless a DLT stack is wired),
- gates X-Ray IAM behind enable_xray.

Network mode (``network_mode``) is a deploy-time choice, default ``public``:
- ``public``: no VPC is created; the runtime uses AWS-managed egress. This is
  the lightweight default (no NAT/endpoint cost, no service-managed ENIs that
  linger up to ~8h on delete). Inbound is still IAM SigV4 only.
- ``vpc``: the runtime ENIs are placed in a private VPC (one NAT, interface/S3
  endpoints, egress-only SG) for egress control, private AWS-service traffic,
  and reaching private/internal test targets (e.g. a private smoke target).
Inbound is IAM SigV4 in BOTH modes — PUBLIC does not expose an inbound endpoint.

Coexists with the CFN stack: the runtime is named ``ai_load_test_gen_cdk`` so it does
not collide with the CFN stack's ``ai_load_test_gen``.

NOTE: in ``vpc`` mode only the create-new-VPC path is implemented here (the CFN's
existing-VPC path is a TODO for the CDK port).
"""
from __future__ import annotations

import os

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class AILoadTestGenStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bedrock_region: str,
        bedrock_model_primary: str = "",
        bedrock_model_fallback: str = "",
        bedrock_profile_arns: list[str] | None = None,
        dlt_stack_name: str = "",
        dlt_region: str = "",
        dlt_api_gateway_arn: str = "",
        dlt_scenarios_bucket_arn: str = "",
        dlt_stack_arn: str = "",
        enable_xray: bool = False,
        container_uri: str = "",
        network_mode: str = "public",
        vpc_cidr: str = "10.60.0.0/16",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        has_dlt = bool(dlt_stack_name)
        is_vpc = str(network_mode).lower() == "vpc"
        bedrock_profile_arns = bedrock_profile_arns or []

        # ------------------------------------------------------------------ #
        # Networking — VPC mode only. In PUBLIC mode nothing below is created:
        # the runtime uses AWS-managed egress and there is no VPC/NAT/endpoint/
        # SG (and no service-managed ENIs to linger on delete). Inbound stays
        # IAM SigV4 in both modes.
        # ------------------------------------------------------------------ #
        vpc = None
        runtime_sg = None
        if is_vpc:
            # Private subnets + one NAT (egress only, no inbound).
            vpc = ec2.Vpc(
                self,
                "Vpc",
                ip_addresses=ec2.IpAddresses.cidr(vpc_cidr),
                max_azs=2,
                nat_gateways=1,
                subnet_configuration=[
                    ec2.SubnetConfiguration(
                        name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                    ),
                    ec2.SubnetConfiguration(
                        name="private",
                        subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                        cidr_mask=20,
                    ),
                ],
            )
            private_subnets = ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )

            # Runtime ENIs: egress only (no inbound => not publicly reachable).
            runtime_sg = ec2.SecurityGroup(
                self,
                "RuntimeSg",
                vpc=vpc,
                allow_all_outbound=True,
                description="AgentCore runtime ENIs - egress only, no inbound",
            )
            # Interface endpoints: accept 443 only from the runtime SG.
            endpoint_sg = ec2.SecurityGroup(
                self,
                "EndpointSg",
                vpc=vpc,
                allow_all_outbound=False,
                description="Interface VPC endpoints - 443 from the runtime SG only",
            )
            endpoint_sg.add_ingress_rule(
                runtime_sg, ec2.Port.tcp(443), "runtime SG to endpoints 443"
            )

            # S3 via gateway endpoint (ECR layers + scenarios/spec buckets).
            vpc.add_gateway_endpoint(
                "S3GatewayEndpoint", service=ec2.GatewayVpcEndpointAwsService.S3
            )

            def _iface(cid: str, svc: ec2.InterfaceVpcEndpointAwsService) -> None:
                vpc.add_interface_endpoint(
                    cid,
                    service=svc,
                    security_groups=[endpoint_sg],
                    subnets=private_subnets,
                    private_dns_enabled=True,
                    open=False,  # ingress governed by endpoint_sg, not the VPC CIDR
                )

            # Required in VPC (no-internet) mode: image pull (ecr.api/ecr.dkr,
            # layers via S3 gw) + runtime logs; bedrock-runtime for model calls.
            _iface("BedrockRuntimeEndpoint", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME)
            _iface("EcrApiEndpoint", ec2.InterfaceVpcEndpointAwsService.ECR)
            _iface("EcrDkrEndpoint", ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER)
            _iface("LogsEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS)
            # DescribeStacks endpoint only when DLT is connected.
            if has_dlt:
                _iface("CfnEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDFORMATION)

        # ------------------------------------------------------------------ #
        # Spec-input bucket (private; TLS-only via enforce_ssl; 30d lifecycle).
        # ------------------------------------------------------------------ #
        spec_bucket = s3.Bucket(
            self,
            "SpecInputBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS_MANAGED,
            versioned=True,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-spec-inputs",
                    expiration=Duration.days(30),
                    noncurrent_version_expiration=Duration.days(7),
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------ #
        # Container image — DockerImageAsset (ARM64). No CodeBuild/ECR/Lambda.
        # container_uri context skips the build (fast synth / CI / BYO image).
        # ------------------------------------------------------------------ #
        image_repo = None
        if container_uri:
            image_uri = container_uri
        else:
            image_asset = ecr_assets.DockerImageAsset(
                self,
                "AgentImage",
                directory=_REPO_ROOT,
                platform=ecr_assets.Platform.LINUX_ARM64,
                # Keep the build context minimal and, critically, exclude the CDK
                # output/venv (they live under the repo root) — otherwise the
                # asset staging copies cdk.out into itself recursively -> ENAMETOOLONG.
                exclude=[
                    ".git",
                    "infrastructure",
                    "cdk.out",
                    "**/cdk.out",
                    ".venv",
                    "**/.venv",
                    "**/__pycache__",
                    "**/*.pyc",
                    ".mypy_cache",
                    "test",
                    "specs",
                    "sample",
                    "sample-data",
                    "node_modules",
                ],
            )
            image_uri = image_asset.image_uri
            image_repo = image_asset.repository

        # ------------------------------------------------------------------ #
        # Runtime execution role — least privilege (mirrors the CFN role).
        # ------------------------------------------------------------------ #
        role = iam.Role(
            self,
            "RuntimeExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:{self.partition}:bedrock-agentcore:"
                            f"{self.region}:{self.account}:*"
                        )
                    },
                },
            ),
            description="AI Load Test Generator Agent runtime execution role (least privilege)",
        )

        # Bedrock invoke — the selected inference-profile + foundation-model ARNs.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockInvoke",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=bedrock_profile_arns or ["*"],
            )
        )
        # Spec-input bucket read (swagger ingress via s3:// URI path).
        role.add_to_policy(
            iam.PolicyStatement(
                sid="SpecInputRead",
                actions=["s3:GetObject"],
                resources=[spec_bucket.arn_for_objects("*")],
            )
        )
        # ECR pull (image) + auth token (star-only action).
        if image_repo is not None:
            image_repo.grant_pull(role)
        else:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="EcrPull",
                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    resources=["*"],
                )
            )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrAuthToken",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],  # no resource-level scoping for this action
            )
        )
        # Runtime logs.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogs",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:{self.partition}:logs:{self.region}:{self.account}:"
                    "log-group:/aws/bedrock-agentcore/runtimes/*"
                ],
            )
        )
        # CloudWatch metrics (namespace-scoped) + workload identity token.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchMetrics",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WorkloadIdentity",
                actions=["bedrock-agentcore:GetWorkloadAccessToken"],
                resources=["*"],
            )
        )
        # X-Ray — opt-in only; star-only actions.
        if enable_xray:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="XRayExport",
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    resources=["*"],
                )
            )
        # DLT — only when connected (HasDlt).
        if has_dlt:
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="DltApiInvoke",
                    actions=["execute-api:Invoke"],
                    resources=[dlt_api_gateway_arn],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="DltDescribeStack",
                    actions=["cloudformation:DescribeStacks"],
                    resources=[dlt_stack_arn],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    sid="DltScenariosBucketWrite",
                    actions=["s3:PutObject", "s3:GetObject"],
                    resources=[f"{dlt_scenarios_bucket_arn}/public/test-scenarios/*"],
                )
            )

        # ------------------------------------------------------------------ #
        # AgentCore runtime (L1) — VPC-private, container artifact.
        # ------------------------------------------------------------------ #
        env_vars = {
            "BEDROCK_REGION": bedrock_region,
            "BEDROCK_MODEL_PRIMARY": bedrock_model_primary,
            "BEDROCK_MODEL_FALLBACK": bedrock_model_fallback,
            "SPEC_INPUT_BUCKET": spec_bucket.bucket_name,
        }
        if has_dlt:
            env_vars["DLT_STACK_NAME"] = dlt_stack_name
            env_vars["DLT_REGION"] = dlt_region

        # Network mode: VPC places ENIs in private subnets; PUBLIC uses
        # AWS-managed egress (no VPC). Inbound is IAM SigV4 in both.
        if is_vpc:
            network_configuration = {
                "NetworkMode": "VPC",
                "NetworkModeConfig": {
                    "SecurityGroups": [runtime_sg.security_group_id],
                    "Subnets": vpc.select_subnets(
                        subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                    ).subnet_ids,
                },
            }
        else:
            network_configuration = {"NetworkMode": "PUBLIC"}

        runtime = cdk.CfnResource(
            self,
            "AgentRuntime",
            type="AWS::BedrockAgentCore::Runtime",
            properties={
                "AgentRuntimeName": "ai_load_test_gen_cdk",  # distinct from the CFN stack's ai_load_test_gen
                "RoleArn": role.role_arn,
                "ProtocolConfiguration": "HTTP",
                "AgentRuntimeArtifact": {
                    "ContainerConfiguration": {"ContainerUri": image_uri}
                },
                "NetworkConfiguration": network_configuration,
                "EnvironmentVariables": env_vars,
            },
        )

        # AgentCore validates the execution role can pull the ECR image at
        # runtime-create time. grant_pull() adds those perms to the role's
        # (separate) DefaultPolicy resource; make the runtime depend on the role
        # so that policy is attached first. In VPC mode the subnet/SG/endpoint
        # resources provided enough ordering slack to hide this, but in PUBLIC
        # mode there are none — so the dependency must be explicit.
        runtime.node.add_dependency(role)

        cdk.CfnOutput(
            self,
            "AgentRuntimeArn",
            value=runtime.get_att("AgentRuntimeArn").to_string(),
            description="ARN of the AgentCore runtime (use with InvokeAgentRuntime).",
        )
        cdk.CfnOutput(
            self,
            "SpecInputBucketName",
            value=spec_bucket.bucket_name,
            description="Upload swagger/OpenAPI here; reference its s3:// URI in the prompt.",
        )
